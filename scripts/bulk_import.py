"""Bulk import cards from CSV file.

Usage:
    python -m scripts.bulk_import data.csv --user-id 123456789
    python scripts/bulk_import.py data.csv --user-id 123456789

The user-id is the Telegram user ID. If the user doesn't exist in the
database, they will be created.

CSV format (header row required):
    front,back,image,tags

See src/services/bulk_import.py for full format documentation.

Example CSV:
    front,back,image,tags
    hello,привіт,images/hello.jpg,"greetings,basics"
    world,світ,https://example.com/world.jpg,
    cat,кіт,,animals

Environment variables (same as the bot):
    OPENAI_API_KEY - Required for pronunciation generation
    DB_PATH - Database path (default: srs.db)
    IMAGE_DIR - Image storage directory (default: images)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Allow `python scripts/bulk_import.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env file from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.db.crud import get_or_create_user  # noqa: E402
from src.db.engine import init_db, session_scope  # noqa: E402
from src.services.bulk_import import import_from_csv_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk import cards from CSV file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "csv_file",
        type=Path,
        help="Path to CSV file (front,back,image,tags)",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        required=True,
        help="Telegram user ID to import cards for",
    )
    args = parser.parse_args()

    # Validate CSV file
    csv_path: Path = args.csv_file
    if not csv_path.exists():
        print(f"Error: CSV file not found: {csv_path}", file=sys.stderr)
        return 1

    # Check API key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: OPENAI_API_KEY is not set.", file=sys.stderr)
        return 1

    # Initialize database
    db_path = Path(os.environ.get("DB_PATH", "srs.db"))
    image_dir = Path(os.environ.get("IMAGE_DIR", "images"))
    init_db(db_path)

    print(f"Importing from: {csv_path}")
    print(f"Target user ID: {args.user_id}")
    print(f"Image directory: {image_dir}")
    print()

    with session_scope() as session:
        # Get or create user (CLI has no username/timezone info, use defaults)
        user = get_or_create_user(
            session,
            telegram_id=args.user_id,
            username=None,
            tz="UTC",
        )
        print(f"User: #{user.id} (telegram_id={user.telegram_id})")

        # Import cards
        result = import_from_csv_file(
            session=session,
            user=user,
            csv_path=csv_path.resolve(),
            api_key=api_key,
            image_dir=image_dir,
        )

    # Print results
    print()
    print("=" * 40)
    print(f"Total rows:  {result.total}")
    print(f"Imported:    {result.success}")
    print(f"Failed:      {result.failed}")

    if result.errors:
        print()
        print("Errors:")
        for err in result.errors:
            print(f"  Row {err.row_num}: {err.front!r} - {err.reason}")

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
