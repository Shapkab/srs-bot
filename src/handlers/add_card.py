"""/add, /addm, and /addimage handlers.

  /add front | back              one-shot, fast for power users
  /addm                          prompts for front, then back, then optional tags
  /addimage                      prompts for front+back, each may be a photo
                                 (with optional caption) or plain text

All three coexist so the muscle-memory ``/add`` UX is unaffected.
"""

from __future__ import annotations

import io
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope
from src.utils.image_store import store_bytes
from src.utils.pronunciation import generate_pronunciation

log = logging.getLogger(__name__)

router = Router(name="add_card")

# Shown whenever IPA generation fails after all retries. Card creation
# is blocked on success, so the user simply retries.
_PRONUNCIATION_FAIL_MSG = "Failed to generate pronunciation. Please try again."


class AddCardFSM(StatesGroup):
    """States for /addm. ``tags`` is optional — user can /skip."""

    front = State()
    back = State()
    tags = State()


@router.message(Command("add"))
async def cmd_add(message: Message, settings: Settings) -> None:
    # message.text starts with "/add ". Strip the command, split on "|".
    text = (message.text or "").removeprefix("/add").strip()
    if "|" not in text:
        await message.answer(
            "Use the format: <code>/add front | back</code>\n"
            "Example: <code>/add reach out to | contact someone, usually for help</code>"
        )
        return

    front, back = (part.strip() for part in text.split("|", 1))
    if not front or not back:
        await message.answer("Both front and back are required.")
        return

    # Block card creation on pronunciation: generate IPA first, abort
    # the /add entirely if OpenAI fails after its retries.
    try:
        ipa = generate_pronunciation(front, settings.openai_api_key)
    except Exception:
        log.warning("pronunciation failed for /add", exc_info=True)
        await message.answer(_PRONUNCIATION_FAIL_MSG)
        return

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = add_card(s, user, front=front, back=back, front_pronunciation=ipa)
        card_id = card.id

    await message.answer(f"Added card #{card_id}. It will appear in your next /review.")


# --- /addm (multi-step FSM) -------------------------------------------------


@router.message(Command("addm"))
async def cmd_addm_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddCardFSM.front)
    await message.answer("Send the <b>front</b> of the card (or /cancel).")


@router.message(Command("cancel"), AddCardFSM.front)
@router.message(Command("cancel"), AddCardFSM.back)
@router.message(Command("cancel"), AddCardFSM.tags)
async def cmd_addm_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.")


@router.message(AddCardFSM.front)
async def cmd_addm_front(message: Message, state: FSMContext) -> None:
    front = (message.text or "").strip()
    if not front:
        await message.answer("Front cannot be empty. Try again, or /cancel.")
        return
    await state.update_data(front=front)
    await state.set_state(AddCardFSM.back)
    await message.answer("Now send the <b>back</b> (or /cancel).")


@router.message(AddCardFSM.back)
async def cmd_addm_back(message: Message, state: FSMContext) -> None:
    back = (message.text or "").strip()
    if not back:
        await message.answer("Back cannot be empty. Try again, or /cancel.")
        return
    await state.update_data(back=back)
    await state.set_state(AddCardFSM.tags)
    await message.answer("Send tags (comma-separated), or /skip for none.")


@router.message(Command("skip"), AddCardFSM.tags)
async def cmd_addm_skip_tags(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    await _finalize(message, state, settings, tags=None)


@router.message(AddCardFSM.tags)
async def cmd_addm_tags(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    tags = (message.text or "").strip() or None
    await _finalize(message, state, settings, tags=tags)


async def _finalize(
    message: Message,
    state: FSMContext,
    settings: Settings,
    tags: str | None,
) -> None:
    data = await state.get_data()
    front = data.get("front")
    back = data.get("back")
    await state.clear()
    if not front or not back:
        # Shouldn't normally happen — the per-state validators block empties.
        await message.answer("Lost the in-progress card. Start again with /addm.")
        return

    try:
        ipa = generate_pronunciation(front, settings.openai_api_key)
    except Exception:
        log.warning("pronunciation failed for /addm", exc_info=True)
        await message.answer(_PRONUNCIATION_FAIL_MSG)
        return

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = add_card(
            s, user, front=front, back=back, tags=tags, front_pronunciation=ipa
        )
        card_id = card.id

    await message.answer(
        f"Added card #{card_id}. It will appear in your next /review."
    )


# --- /addimage (multi-step FSM with optional photos) ------------------------


class AddImageFSM(StatesGroup):
    """States for /addimage. Each side accepts EITHER a photo (with an
    optional caption used as the text) OR a plain text message."""

    image_front = State()
    image_back = State()


@router.message(Command("addimage"))
async def cmd_addimage_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddImageFSM.image_front)
    await message.answer(
        "Send the <b>front</b> of the card.\n"
        "Reply with a photo (optionally with a caption) or with plain "
        "text. /cancel to abort."
    )


@router.message(Command("cancel"), AddImageFSM.image_front)
@router.message(Command("cancel"), AddImageFSM.image_back)
async def cmd_addimage_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Cancelled.")


def _largest_photo_file_id(message: Message) -> str:
    """Telegram delivers photos as a list of sizes, smallest first.
    The last entry is the largest."""
    assert message.photo, "caller must check F.photo before this point"
    return message.photo[-1].file_id


@router.message(AddImageFSM.image_front, F.photo)
async def cmd_addimage_front_photo(message: Message, state: FSMContext) -> None:
    file_id = _largest_photo_file_id(message)
    caption = (message.caption or "").strip()
    if not caption:
        # Cards always carry text on each side — the caption is what
        # /review will render alongside the photo. Don't transition: the
        # user re-sends in the same state.
        await message.answer(
            "Photos need a caption (the text shown alongside the image). "
            "Re-send the photo with a caption, or /cancel."
        )
        return
    await state.update_data(front_file_id=file_id, front_text=caption)
    await state.set_state(AddImageFSM.image_back)
    await message.answer(
        "Got the front. Now send the <b>back</b> (photo with optional "
        "caption, or text). /cancel to abort."
    )


@router.message(AddImageFSM.image_front, F.text)
async def cmd_addimage_front_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Front cannot be empty. Try again, or /cancel.")
        return
    await state.update_data(front_file_id=None, front_text=text)
    await state.set_state(AddImageFSM.image_back)
    await message.answer(
        "Got the front. Now send the <b>back</b> (photo with optional "
        "caption, or text). /cancel to abort."
    )


@router.message(AddImageFSM.image_back, F.photo)
async def cmd_addimage_back_photo(
    message: Message, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    file_id = _largest_photo_file_id(message)
    caption = (message.caption or "").strip()
    if not caption:
        await message.answer(
            "Photos need a caption (the text shown alongside the image). "
            "Re-send the photo with a caption, or /cancel."
        )
        return
    await _finalize_image(
        message, state, bot, settings,
        back_file_id=file_id, back_text=caption,
    )


@router.message(AddImageFSM.image_back, F.text)
async def cmd_addimage_back_text(
    message: Message, state: FSMContext, bot: Bot, settings: Settings
) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Back cannot be empty. Try again, or /cancel.")
        return
    await _finalize_image(
        message, state, bot, settings,
        back_file_id=None, back_text=text,
    )


async def _download_and_hash(bot: Bot, file_id: str, settings: Settings) -> str:
    """Pull the photo bytes from Telegram into memory, write to
    ``IMAGE_DIR`` keyed by SHA-256, and return the hex hash.

    Dedup is handled inside ``store_bytes`` — identical content writes
    once even if uploaded twice.

    Raises:
      ``ValueError`` — payload exceeds ``settings.max_image_bytes``;
        the calling handler surfaces a distinct "too big" message.
      ``RuntimeError`` — any other failure during download or local
        store. The broad catch is intentional: aiogram exposes several
        layers (network, Telegram API, local I/O) and we surface the
        same user-recoverable message ("try /addimage again") for all.
    """
    try:
        buf = io.BytesIO()
        await bot.download(file_id, destination=buf)
        if buf.tell() > settings.max_image_bytes:
            raise ValueError("image too large")
        return store_bytes(settings.image_dir, buf.getvalue())
    except ValueError:
        raise
    except Exception as e:
        log.warning(
            "image_download_failed",
            exc_info=True,
            extra={"event": "image_download_failed"},
        )
        raise RuntimeError("image_download_failed") from e


async def _finalize_image(
    message: Message,
    state: FSMContext,
    bot: Bot,
    settings: Settings,
    *,
    back_file_id: str | None,
    back_text: str,
) -> None:
    data = await state.get_data()
    front_file_id: str | None = data.get("front_file_id")
    front_text: str = data.get("front_text") or ""
    await state.clear()

    if not front_text and not front_file_id:
        await message.answer("Lost the in-progress card. Start again with /addimage.")
        return

    front_sha: str | None = None
    back_sha: str | None = None
    try:
        if front_file_id is not None:
            front_sha = await _download_and_hash(bot, front_file_id, settings)
        if back_file_id is not None:
            back_sha = await _download_and_hash(bot, back_file_id, settings)
    except ValueError:
        # Payload bigger than settings.max_image_bytes.
        await message.answer(
            "This image is too big — max is 5 MB. Start again with /addimage."
        )
        return
    except RuntimeError:
        # Network / Telegram-API / local-I/O failure. Already logged at
        # WARNING with event=image_download_failed in _download_and_hash.
        await message.answer(
            "Couldn't fetch the image, please try /addimage again."
        )
        return

    # Block card creation on pronunciation. The image bytes already
    # landed under IMAGE_DIR — that's harmless (content-addressed, reused
    # if re-uploaded); the card simply isn't created on failure.
    try:
        ipa = generate_pronunciation(front_text, settings.openai_api_key)
    except Exception:
        log.warning("pronunciation failed for /addimage", exc_info=True)
        await message.answer(_PRONUNCIATION_FAIL_MSG)
        return

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = add_card(
            s, user,
            front=front_text, back=back_text,
            front_pronunciation=ipa,
            front_image_file_id=front_file_id, front_image_sha256=front_sha,
            back_image_file_id=back_file_id, back_image_sha256=back_sha,
        )
        card_id = card.id

    log.info(
        "Added image card",
        extra={
            "event": "add_image_card",
            "card_id": card_id,
            "user_id": user.id,
        },
    )
    await message.answer(
        f"Added card #{card_id}. It will appear in your next /review."
    )
