"""Content-addressed local storage for card-attached images.

One file per image, named by SHA-256 of the byte stream:

    <IMAGE_DIR>/<64 hex chars>.jpg

A given byte stream therefore deduplicates automatically — uploading the
same photo to two different cards costs one file on disk and one row
update on each card. Always written as ``.jpg`` because Telegram serves
photos as JPEG and we deliberately do not try to detect other formats.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path


def sha256_hex(data: bytes) -> str:
    """Hex digest of SHA-256 over the full byte stream."""
    return hashlib.sha256(data).hexdigest()


def store_bytes(image_dir: Path, data: bytes) -> str:
    """Write ``data`` to ``image_dir/<sha256>.jpg`` atomically.
    Returns the hex SHA-256.

    Write goes to ``<sha>.jpg.tmp`` then ``os.replace`` to the final
    name — POSIX-atomic, so a concurrent writer landing the same bytes
    never observes a half-written file. Content addressing makes the
    overwrite idempotent: identical bytes always produce the same path
    and the same final state, so back-to-back writes don't corrupt.
    """
    sha = sha256_hex(data)
    image_dir.mkdir(parents=True, exist_ok=True)
    target = image_dir / f"{sha}.jpg"
    tmp = target.parent / f"{target.name}.tmp"
    tmp.write_bytes(data)
    # A concurrent writer with identical content (same sha → same
    # filename) may have finalized the target via its own os.replace
    # already and taken the shared .tmp with it. That's safe — the
    # bytes are content-addressed, so the final state is identical
    # whichever writer "wins".
    with contextlib.suppress(FileNotFoundError):
        os.replace(tmp, target)
    return sha
