"""Local single-run lock (protects against overlapping runs on one machine).

On GitHub Actions the workflow-level ``concurrency`` group is the primary guard; this lock
covers local/cron use. Duplicates that still slip through (e.g. a local run overlapping a
scheduled one) are healed by the de-duplication pass in every sync.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path


class LockHeld(Exception):
    pass


@contextlib.contextmanager
def run_lock(
    name: str = "tuf-sync", stale_after_s: int = 2 * 3600, directory: str | None = None
) -> Iterator[Path]:
    path = Path(directory or tempfile.gettempdir()) / f"{name}.lock"
    if path.exists() and time.time() - path.stat().st_mtime > stale_after_s:
        path.unlink(missing_ok=True)  # a crashed run left it behind
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise LockHeld(f"another TUF sync appears to be running (lock file {path})") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield path
    finally:
        path.unlink(missing_ok=True)
