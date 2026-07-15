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
    BOT_TOKEN - Required for image registration (if CSV has images)
    DB_PATH - Database path (default: srs.db)
    IMAGE_DIR - Image storage directory (default: images)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Allow `python scripts/bulk_import.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env file from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402

from src.db.crud import get_or_create_user  # noqa: E402
from src.db.engine import init_db, session_scope  # noqa: E402
from src.services.bulk_import import import_from_csv_file  # noqa: E402


async def async_main() -> int:
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

    # Check bot token (required if CSV has images)
    bot_token = os.environ.get("BOT_TOKEN")
    bot: Bot | None = None
    if bot_token:
        bot = Bot(
            token=bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        print("Bot token found - images will be registered with Telegram")
    else:
        print("Warning: BOT_TOKEN not set - images will be stored locally but not registered")
        print("         (run scripts/reupload_images.py after setting BOT_TOKEN)")

    # Initialize database
    db_path = Path(os.environ.get("DB_PATH", "srs.db"))
    image_dir = Path(os.environ.get("IMAGE_DIR", "images"))
    init_db(db_path)

    print(f"Importing from: {csv_path}")
    print(f"Target user ID: {args.user_id}")
    print(f"Image directory: {image_dir}")
    print()

    try:
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
            result = await import_from_csv_file(
                session=session,
                user=user,
                csv_path=csv_path.resolve(),
                api_key=api_key,
                image_dir=image_dir,
                bot=bot,
                owner_telegram_id=args.user_id if bot else None,
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
    finally:
        if bot:
            await bot.session.close()


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
