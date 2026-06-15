"""Bulk import cards from CSV.

CSV format (header row required):
    front,back,image,tags

- ``front``: required, the question/word
- ``back``: required, the answer/translation
- ``image``: optional, path to local file OR http(s) URL; attached as back_image
- ``tags``: optional, comma-separated tags

Image column accepts:
- Absolute path: /home/user/images/hello.jpg
- Relative path: images/hello.jpg (resolved relative to CSV file location)
- URL: https://example.com/hello.jpg

Example:
    front,back,image,tags
    hello,привіт,images/hello.jpg,"greetings,basics"
    world,світ,https://example.com/world.jpg,
    cat,кіт,,animals
"""

from __future__ import annotations

import csv
import logging
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from src.db.crud import add_card
from src.db.models import User
from src.utils.image_store import store_bytes
from src.utils.pronunciation import generate_pronunciation

log = logging.getLogger(__name__)

# Maximum image size to download from URLs (5 MB)
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# Timeout for URL downloads (seconds)
URL_TIMEOUT = 30


@dataclass
class ImportError:
    """A single row that failed to import."""

    row_num: int
    front: str
    reason: str


@dataclass
class ImportResult:
    """Summary of a bulk import run."""

    total: int = 0
    success: int = 0
    failed: int = 0
    errors: list[ImportError] = field(default_factory=list)

    def add_success(self) -> None:
        self.total += 1
        self.success += 1

    def add_failure(self, row_num: int, front: str, reason: str) -> None:
        self.total += 1
        self.failed += 1
        self.errors.append(ImportError(row_num=row_num, front=front, reason=reason))


def _download_image_url(url: str) -> bytes:
    """Download image from URL with timeout and size limit."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "srs-bot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=URL_TIMEOUT) as resp:
        data = resp.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES // 1024 // 1024}MB limit")
        return data


def _load_image(image_path: str, csv_dir: Path) -> bytes | None:
    """Load image from local path or URL.

    Returns None if image_path is empty.
    Raises on download/read failure.
    """
    if not image_path or not image_path.strip():
        return None

    image_path = image_path.strip()

    # URL
    if image_path.startswith(("http://", "https://")):
        return _download_image_url(image_path)

    # Local file - resolve relative to CSV directory
    path = Path(image_path)
    if not path.is_absolute():
        path = csv_dir / path

    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image exceeds {MAX_IMAGE_BYTES // 1024 // 1024}MB limit")
    return data


def import_cards(
    session: Session,
    user: User,
    rows: list[dict[str, str]],
    api_key: str,
    image_dir: Path,
    csv_dir: Path | None = None,
) -> ImportResult:
    """Import cards from parsed CSV rows.

    Args:
        session: SQLAlchemy session (caller manages commit/rollback)
        user: Target user for the cards
        rows: List of dicts with keys: front, back, image, tags
        api_key: OpenAI API key for pronunciation
        image_dir: Directory to store downloaded images
        csv_dir: Base directory for resolving relative image paths

    Returns:
        ImportResult with success/failure counts and error details
    """
    result = ImportResult()
    csv_dir = csv_dir or Path.cwd()

    for i, row in enumerate(rows, start=2):  # Row 1 is header
        front = (row.get("front") or "").strip()
        back = (row.get("back") or "").strip()
        image = (row.get("image") or "").strip()
        tags = (row.get("tags") or "").strip() or None

        # Validate required fields
        if not front:
            result.add_failure(i, "(empty)", "Missing front")
            continue
        if not back:
            result.add_failure(i, front, "Missing back")
            continue

        # Load and store image
        back_image_sha: str | None = None
        try:
            image_data = _load_image(image, csv_dir)
            if image_data:
                back_image_sha = store_bytes(image_dir, image_data)
        except Exception as e:
            result.add_failure(i, front, f"Image error: {e}")
            continue

        # Generate pronunciation
        try:
            ipa = generate_pronunciation(front, api_key)
        except Exception as e:
            result.add_failure(i, front, f"Pronunciation error: {e}")
            continue

        # Create card
        try:
            add_card(
                session,
                user,
                front=front,
                back=back,
                tags=tags,
                source="bulk_import",
                front_pronunciation=ipa,
                back_image_sha256=back_image_sha,
            )
            result.add_success()
            log.info(f"Imported: {front}")
        except Exception as e:
            result.add_failure(i, front, f"DB error: {e}")
            continue

    return result


def import_from_csv_file(
    session: Session,
    user: User,
    csv_path: Path,
    api_key: str,
    image_dir: Path,
) -> ImportResult:
    """Import cards from a CSV file.

    Args:
        session: SQLAlchemy session (caller manages commit/rollback)
        user: Target user for the cards
        csv_path: Path to the CSV file
        api_key: OpenAI API key for pronunciation
        image_dir: Directory to store downloaded images

    Returns:
        ImportResult with success/failure counts and error details
    """
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    return import_cards(
        session=session,
        user=user,
        rows=rows,
        api_key=api_key,
        image_dir=image_dir,
        csv_dir=csv_path.parent,
    )
