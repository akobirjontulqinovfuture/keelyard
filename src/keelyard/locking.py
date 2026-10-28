"""A small cross-platform inter-process lock (hooks can fire concurrently)."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, timeout: float = 30.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    try:
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import msvcrt


            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"could not lock {path}") from None
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"could not lock {path}") from None
                    time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # closing releases the lock on both platforms
