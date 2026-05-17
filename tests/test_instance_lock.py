"""Integration test for the POSIX flock-based instance lock.

A subprocess takes and holds the lock, then we attempt to acquire it
in-process and assert ``InstanceAlreadyRunning``. The subprocess is
killed afterward to release the kernel-side lock.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.instance_lock import (
    InstanceAlreadyRunning,
    instance_lock,
    lock_path_for,
)


def _spawn_lock_holder(lock_path: Path) -> subprocess.Popen[bytes]:
    """Spawn a child that acquires the same flock and sits idle."""
    code = (
        "import fcntl, sys, time;\n"
        "fd = open(sys.argv[1], 'a+b');\n"
        "fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB);\n"
        "sys.stdout.write('LOCKED\\n');\n"
        "sys.stdout.flush();\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(lock_path)],
        stdout=subprocess.PIPE,
    )
    # Wait for the child to confirm it has the lock.
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert b"LOCKED" in line, f"child did not lock; got: {line!r}"
    return proc


def test_second_instance_is_blocked(tmp_path: Path) -> None:
    lock = tmp_path / "live.db.lock"
    proc = _spawn_lock_holder(lock)
    try:
        with pytest.raises(InstanceAlreadyRunning), instance_lock(lock):
            pytest.fail("should not have acquired the lock")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_lock_released_when_holder_dies(tmp_path: Path) -> None:
    lock = tmp_path / "live.db.lock"
    proc = _spawn_lock_holder(lock)
    proc.terminate()
    proc.wait(timeout=5)
    # Tiny grace period: kernel reclaims the flock on process exit.
    time.sleep(0.05)

    # Now we should be able to acquire it.
    with instance_lock(lock):
        pass  # acquired and released cleanly


def test_lock_released_on_context_exit(tmp_path: Path) -> None:
    lock = tmp_path / "live.db.lock"
    with instance_lock(lock):
        pass
    # Second acquisition in the same process must succeed.
    with instance_lock(lock):
        pass


def test_lock_path_for_appends_dot_lock(tmp_path: Path) -> None:
    db = tmp_path / "srs.db"
    assert lock_path_for(db).name == "srs.db.lock"
