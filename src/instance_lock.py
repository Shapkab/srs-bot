"""POSIX flock-based instance lock.

Prevents two bot instances from polling Telegram concurrently against
the same DB (which would result in duplicated messages and racy DB
writes). The lockfile lives next to the DB file:

    <DB_PATH>.lock      (LOCK_EX | LOCK_NB)

Posix-only. Windows is not supported in v1.
"""

from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO


class InstanceAlreadyRunning(RuntimeError):
    """Another process holds the instance lock."""


def _open_for_lock(lock_path: Path) -> IO[bytes]:
    # 'a+b' (append, binary) is the canonical choice: creates the file if
    # missing without truncating an existing one. We don't write to it —
    # the lock state lives in the kernel, not the file contents.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return lock_path.open("a+b")


@contextmanager
def instance_lock(lock_path: Path) -> Iterator[None]:
    """Acquire an exclusive non-blocking flock on ``lock_path``.

    Raises ``InstanceAlreadyRunning`` if another process holds it. The
    lock is released on context exit (or when the process dies, which
    is the whole point of using flock rather than an in-process Lock).
    """
    fd = _open_for_lock(lock_path)
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise InstanceAlreadyRunning(
                f"another instance is already running (lock: {lock_path})"
            ) from e
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()


def lock_path_for(db_path: Path) -> Path:
    """Convention: lockfile sits next to the DB with the ``.lock`` suffix."""
    return db_path.with_suffix(db_path.suffix + ".lock")
