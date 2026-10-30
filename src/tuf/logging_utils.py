"""Secret-safe, optionally structured (JSON) logging.

TUF logs counts and identifiers, never expense descriptions or amounts at INFO level,
because GitHub Actions logs of public repositories are publicly readable.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable

_TOKEN_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"\b(secret_|ntn_)[A-Za-z0-9]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
]


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "[REDACTED]")
        for pattern in _TOKEN_PATTERNS:
            text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "text", secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter(secrets))
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
