"""A small retrying JSON HTTP client shared by the Splitwise, Notion and FX connectors.

Retry policy (bounded, never infinite):

* 429 and 529: always retried (the server rejected the request), honouring ``Retry-After``.
* 500/502/503/504 and read timeouts: retried only for idempotent requests, because a
  non-idempotent write may already have been applied.
* Connection failures before a request is sent: always retried.
* Everything else (4xx) fails immediately with an ``ApiError``.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

_IDEMPOTENT = {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
_ALWAYS_RETRY = {429, 529}
_RETRY_IF_IDEMPOTENT = {500, 502, 503, 504}


class ApiError(Exception):
    def __init__(
        self,
        service: str,
        status: int | None,
        message: str,
        *,
        code: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.service = service
        self.status = status
        self.code = code
        self.retryable = retryable
        detail = f"{service} API error"
        if status is not None:
            detail += f" {status}"
        if code:
            detail += f" ({code})"
        super().__init__(f"{detail}: {message}")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 1.0
    max_delay: float = 60.0


class HttpClient:
    def __init__(
        self,
        service: str,
        base_url: str,
        headers: Mapping[str, str],
        *,
        timeout: float = 30.0,
        policy: RetryPolicy | None = None,
        min_interval: float = 0.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.service = service
        self.policy = policy or RetryPolicy()
        self.min_interval = min_interval
        self._sleep = sleep
        self._last_request = 0.0
        self.request_count = 0
        self._client = httpx.Client(
            base_url=base_url, headers=dict(headers), timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        idempotent: bool | None = None,
    ) -> Any:
        method = method.upper()
        is_idempotent = method in _IDEMPOTENT if idempotent is None else idempotent
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            try:
                response = self._client.request(method, path, params=params, json=json, files=files)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                self._maybe_retry(attempt, None, f"connection failed: {type(exc).__name__}")
                continue
            except httpx.TimeoutException as exc:
                if not is_idempotent:
                    raise ApiError(
                        self.service, None, f"timeout: {type(exc).__name__}", retryable=True
                    ) from exc
                self._maybe_retry(attempt, None, f"timeout: {type(exc).__name__}")
                continue
            except httpx.HTTPError as exc:
                raise ApiError(
                    self.service, None, f"transport error: {type(exc).__name__}", retryable=True
                ) from exc

            status = response.status_code
            if status < 400:
                if not response.content:
                    return None
                return response.json()

            code, message = _error_details(response)
            retryable = status in _ALWAYS_RETRY or (
                status in _RETRY_IF_IDEMPOTENT and is_idempotent
            )
            if code == "public_api_request_blocked":
                retryable = False
            if not retryable or attempt >= self.policy.max_attempts:
                raise ApiError(
                    self.service,
                    status,
                    message,
                    code=code,
                    retryable=status in _ALWAYS_RETRY | _RETRY_IF_IDEMPOTENT,
                )
            delay = _retry_after(response) or self._backoff(attempt)
            log.warning(
                "%s %s -> %s; retry %d/%d in %.1fs",
                self.service,
                method,
                status,
                attempt,
                self.policy.max_attempts - 1,
                delay,
            )
            self._sleep(delay)

    def _maybe_retry(self, attempt: int, status: int | None, message: str) -> None:
        if attempt >= self.policy.max_attempts:
            raise ApiError(self.service, status, message, retryable=True)
        delay = self._backoff(attempt)
        log.warning(
            "%s request failed (%s); retry %d in %.1fs", self.service, message, attempt, delay
        )
        self._sleep(delay)

    def _backoff(self, attempt: int) -> float:
        raw = self.policy.base_delay * (2 ** (attempt - 1))
        return float(min(self.policy.max_delay, raw * (0.5 + random.random() / 2)))  # noqa: S311

    def _throttle(self) -> None:
        if self.min_interval > 0:
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                self._sleep(wait)
        self._last_request = time.monotonic()
        self.request_count += 1


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return min(60.0, max(0.0, float(value)))
    except ValueError:
        return None


def _error_details(response: httpx.Response) -> tuple[str | None, str]:
    """Extract an error code/message. Never echoes request data, only the API's message."""
    try:
        body = response.json()
    except ValueError:
        return None, response.reason_phrase or "error"
    if isinstance(body, dict):
        code = body.get("code")
        message = body.get("message") or body.get("error") or body.get("errors")
        if isinstance(message, (list, dict)):
            message = str(message)[:300]
        return (str(code) if code else None), str(message or response.reason_phrase)[:500]
    return None, str(body)[:300]
