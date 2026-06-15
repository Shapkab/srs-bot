"""/import handler for bulk card import via CSV file.

Usage:
    1. Send /import command
    2. Bot prompts for CSV file
    3. User uploads CSV file (as document, not text)
    4. Bot processes and reports results

CSV format (header row required):
    front,back,image,tags

See src/services/bulk_import.py for full format documentation.
"""

from __future__ import annotations

import contextlib
import csv
import io
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from src.config import Settings
from src.db.crud import get_or_create_user
from src.db.engine import session_scope
from src.services.bulk_import import import_cards

log = logging.getLogger(__name__)

router = Router(name="bulk_import")

MAX_CSV_SIZE = 10 * 1024 * 1024  # 10 MB


class ImportFSM(StatesGroup):
    """States for /import."""

    waiting_file = State()


@router.message(Command("import"))
async def cmd_import_start(message: Message, state: FSMContext) -> None:
    """Start the import flow."""
    await state.set_state(ImportFSM.waiting_file)
    await message.answer(
        "Send me a CSV file with your cards.\n\n"
        "<b>Format:</b> <code>front,back,image,tags</code>\n\n"
        "<b>Example:</b>\n"
        "<code>front,back,image,tags\n"
        "hello,привіт,https://example.com/hello.jpg,\"greetings,basics\"\n"
        "world,світ,,\n"
        "cat,кіт,,animals</code>\n\n"
        "The <code>image</code> column accepts URLs (http/https).\n"
        "Send /cancel to abort."
    )


@router.message(Command("cancel"), ImportFSM.waiting_file)
async def cmd_import_cancel(message: Message, state: FSMContext) -> None:
    """Cancel the import."""
    await state.clear()
    await message.answer("Import cancelled.")


@router.message(ImportFSM.waiting_file, F.document)
async def cmd_import_file(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
) -> None:
    """Process uploaded CSV file."""
    await state.clear()

    if not message.from_user:
        return

    doc = message.document
    if not doc:
        await message.answer("Please send a file. Try /import again.")
        return

    # Validate file type
    filename = doc.file_name or ""
    if not filename.lower().endswith(".csv"):
        await message.answer(
            "Please send a CSV file (ending in .csv). Try /import again."
        )
        return

    # Validate file size
    if doc.file_size and doc.file_size > MAX_CSV_SIZE:
        await message.answer(
            f"File too large (max {MAX_CSV_SIZE // 1024 // 1024} MB). "
            "Try /import again with a smaller file."
        )
        return

    # Download file
    try:
        buf = io.BytesIO()
        await bot.download(doc.file_id, destination=buf)
        csv_content = buf.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        await message.answer(
            "Could not read file as UTF-8 text. "
            "Please ensure your CSV is UTF-8 encoded. Try /import again."
        )
        return
    except Exception:
        log.warning("Failed to download CSV file", exc_info=True)
        await message.answer(
            "Failed to download the file. Please try /import again."
        )
        return

    # Parse CSV
    try:
        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
    except csv.Error as e:
        await message.answer(f"Invalid CSV format: {e}\nTry /import again.")
        return

    if not rows:
        await message.answer("CSV file is empty (no data rows). Try /import again.")
        return

    # Validate required columns
    if rows:
        first_row = rows[0]
        if "front" not in first_row or "back" not in first_row:
            await message.answer(
                "CSV must have 'front' and 'back' columns in the header row.\n"
                "Try /import again."
            )
            return

    # Send progress message
    progress_msg = await message.answer(
        f"Processing {len(rows)} cards... This may take a moment."
    )

    # Import cards
    with session_scope() as session:
        user = get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )

        result = import_cards(
            session=session,
            user=user,
            rows=rows,
            api_key=settings.openai_api_key,
            image_dir=settings.image_dir,
            csv_dir=Path.cwd(),  # URLs only for Telegram import
        )

    # Build result message
    lines = [
        "<b>Import complete!</b>\n",
        f"Total rows: {result.total}",
        f"Imported: {result.success}",
        f"Failed: {result.failed}",
    ]

    if result.errors:
        lines.append("\n<b>Errors:</b>")
        # Show first 10 errors to avoid message length limits
        for err in result.errors[:10]:
            front_preview = err.front[:20] + "..." if len(err.front) > 20 else err.front
            lines.append(f"• Row {err.row_num}: <code>{front_preview}</code> — {err.reason}")
        if len(result.errors) > 10:
            lines.append(f"... and {len(result.errors) - 10} more errors")

    # Delete progress message and send result
    with contextlib.suppress(Exception):
        await progress_msg.delete()

    await message.answer("\n".join(lines))

    log.info(
        "Bulk import completed",
        extra={
            "event": "bulk_import",
            "user_id": user.id,
            "total": result.total,
            "success": result.success,
            "failed": result.failed,
        },
    )


@router.message(ImportFSM.waiting_file)
async def cmd_import_invalid(message: Message) -> None:
    """Handle non-file messages while waiting for CSV."""
    await message.answer(
        "Please send a CSV file (as a document attachment), or /cancel to abort."
    )
