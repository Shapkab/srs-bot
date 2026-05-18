"""/review handler and the two callback handlers (show + rate).

Flow:
  1. /review -> show front of next-due card with a "Show answer" button.
  2. User taps "Show answer" -> we reveal the back together with
     Again/Hard/Good/Easy rating buttons.
  3. User taps a rating -> we run FSRS, persist, and immediately show
     the next due card (or a "no cards due" message).

Rendering branches on whether a card has image attachments:

  * Pure-text cards keep the existing "edit one message in place" UX.
  * When the current card has a photo on the front, that message lives
    as a ``send_photo`` and we update it with ``edit_caption``.
  * When the back has a photo, the back is sent as a fresh message and
    the previous message's keyboard is stripped — a photo cannot be
    edited into a text message and vice versa, so we use new messages
    rather than fight the API.
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from src.config import Settings
from src.db.crud import get_or_create_user, next_due_card, persist_review
from src.db.engine import session_scope
from src.db.models import Card, Rating, ReviewState
from src.keyboards.review import rating_kb, show_answer_kb
from src.srs.scheduler import CorruptCardJsonError, apply_review

log = logging.getLogger(__name__)

router = Router(name="review")

# Telegram caps photo captions at 1024 chars; leave headroom for HTML
# tags ("<b></b>" plus the "Rated: ..." prefix used by cb_rate) and
# fall back to a separate text bubble when full_text would exceed this.
_PHOTO_CAPTION_MAX = 1000


def _format_front(card: Card) -> str:
    return f"<b>{html.escape(card.front, quote=False)}</b>"


def _format_front_back(card: Card) -> str:
    return (
        f"<b>{html.escape(card.front, quote=False)}</b>\n\n"
        f"{html.escape(card.back, quote=False)}"
    )


async def _strip_keyboard(message: Message) -> None:
    """Best-effort: drop the inline keyboard from an old message. Used
    when we're about to send a fresh message that supersedes it. The
    catch is intentional — Telegram rejects edits with an unchanged
    payload, and the message may be too old to edit at all."""
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        log.debug("could not strip keyboard", exc_info=True)


@router.message(Command("review"))
async def cmd_review(message: Message, settings: Settings) -> None:
    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        state = next_due_card(s, user)
        if state is None:
            await message.answer("Nothing due. Add cards with /add or come back later.")
            return
        # Capture everything we need while the session is open.
        state_id = state.id
        front_text = _format_front(state.card)
        front_image_file_id = state.card.front_image_file_id

    kb = show_answer_kb(state_id)
    if front_image_file_id is not None:
        await message.answer_photo(
            photo=front_image_file_id, caption=front_text, reply_markup=kb
        )
    else:
        await message.answer(front_text, reply_markup=kb)


@router.callback_query(F.data.startswith("rv:show:"))
async def cb_show_answer(callback: CallbackQuery) -> None:
    state_id = int(callback.data.split(":")[2])

    with session_scope() as s:
        state = s.scalar(select(ReviewState).where(ReviewState.id == state_id))
        if state is None:
            await callback.answer("Card not found.", show_alert=True)
            return
        # Guard against a stale keyboard for a card that has since been
        # /delete'd. Without this, the user could tap "Show answer" on a
        # zombie card and proceed to rate it.
        if state.card.deleted_at is not None:
            await callback.answer("This card was deleted.", show_alert=True)
            return
        full_text = _format_front_back(state.card)
        front_caption_only = _format_front(state.card)
        back_text_only = html.escape(state.card.back, quote=False)
        back_image_file_id = state.card.back_image_file_id

    # Telegram strips .message from callbacks older than ~48h.
    if callback.message is None:
        await callback.answer("Session expired — run /review again.", show_alert=True)
        return

    kb = rating_kb(state_id)
    overlong = len(full_text) > _PHOTO_CAPTION_MAX

    if back_image_file_id is not None:
        # Back is a photo — must be a new message; a text/photo edit
        # cannot change the message type. When the combined caption
        # would exceed Telegram's 1024-char cap, send the photo with
        # just the front-side label and follow up with the back text
        # as a plain message.
        await _strip_keyboard(callback.message)
        if overlong:
            await callback.message.answer_photo(
                photo=back_image_file_id,
                caption=front_caption_only,
                reply_markup=kb,
            )
            await callback.message.answer(back_text_only)
        else:
            await callback.message.answer_photo(
                photo=back_image_file_id, caption=full_text, reply_markup=kb
            )
    elif callback.message.photo:
        # Current message is a photo (front had image), back is text —
        # update in-place by editing the caption. Same overflow rule:
        # if too long, caption stays the front-only label and the back
        # text gets its own bubble.
        if overlong:
            await callback.message.edit_caption(
                caption=front_caption_only, reply_markup=kb
            )
            await callback.message.answer(back_text_only)
        else:
            await callback.message.edit_caption(caption=full_text, reply_markup=kb)
    else:
        # Pure-text path: legacy edit_text behaviour. Telegram's text
        # message cap is 4096 — no fallback needed at our card sizes.
        await callback.message.edit_text(full_text, reply_markup=kb)

    await callback.answer()


async def _surface_corrupt(callback: CallbackQuery, state_id: int) -> None:
    """Tell the user this card is busted and route them to /repair.
    Branches on whether the current message is a photo or text — we
    cannot edit_text() over a photo and vice versa.
    """
    msg = "This card's state is corrupt; it's been skipped — please run /repair"
    if callback.message is None:
        await callback.answer(msg, show_alert=True)
        return
    if callback.message.photo:
        await callback.message.edit_caption(caption=msg, reply_markup=None)
    else:
        await callback.message.edit_text(msg)
    await callback.answer()
    _ = state_id  # state_id retained for future extensions (e.g., one-click /repair)


@router.callback_query(F.data.startswith("rv:rate:"))
async def cb_rate(callback: CallbackQuery, settings: Settings) -> None:
    _, _, state_id_str, rating_str = callback.data.split(":")
    state_id = int(state_id_str)
    rating = Rating(int(rating_str))

    with session_scope() as s:
        state = s.scalar(select(ReviewState).where(ReviewState.id == state_id))
        if state is None:
            await callback.answer("Card not found.", show_alert=True)
            return
        # Guard against a stale keyboard for a card that has since been
        # /delete'd — without this, the rating would write a ReviewLog
        # row + advance ReviewState, resurrecting the "deleted" card.
        if state.card.deleted_at is not None:
            await callback.answer("This card was deleted.", show_alert=True)
            return

        try:
            result = apply_review(
                card_json=state.card_json,
                prev_due=state.due,
                prev_last_review=state.last_review,
                rating=rating,
            )
        except CorruptCardJsonError as e:
            log.warning(
                "corrupt card_json on state_id=%s: %s", state.id, e.original
            )
            await _surface_corrupt(callback, state_id)
            return

        applied = persist_review(s, state, rating, result)
        if not applied:
            # Double-tap (or other concurrent click) already advanced the row.
            await callback.answer("Already rated.", show_alert=False)
            return

        # Look up the next due card in the same transaction so the
        # user sees an uninterrupted stream. Capture everything we need
        # while the session is still open.
        user = state.user
        next_state = next_due_card(s, user)
        if next_state is not None:
            next_state_id = next_state.id
            next_front_text = _format_front(next_state.card)
            next_front_image_file_id = next_state.card.front_image_file_id
        else:
            next_state_id = None
            next_front_text = None
            next_front_image_file_id = None

    # Telegram strips .message from callbacks older than ~48h. The DB write
    # above already happened, so we only skip the inline "next card" UI.
    if callback.message is None:
        await callback.answer("Session expired — run /review again.", show_alert=True)
        return

    current_is_photo = bool(callback.message.photo)
    next_has_image = next_front_image_file_id is not None
    rated_line = f"Rated: <b>{rating.name.title()}</b>"

    if not current_is_photo and not next_has_image:
        # Pure-text path: legacy edit_text behaviour preserved.
        if next_state_id is not None:
            await callback.message.edit_text(
                f"{rated_line}\n\n---\n\n{next_front_text}",
                reply_markup=show_answer_kb(next_state_id),
            )
        else:
            await callback.message.edit_text(
                f"{rated_line}\n\nNothing more due. Nice work."
            )
        await callback.answer()
        return

    # Image path: strip the keyboard from the just-rated message, then
    # send the next card as a fresh message (photo or text). The "Rated"
    # confirmation rides on the next card's caption / text so the user
    # gets the same one-screen UX, just in a new bubble.
    await _strip_keyboard(callback.message)

    if next_state_id is None:
        await callback.message.answer(f"{rated_line}\n\nNothing more due. Nice work.")
    elif next_has_image:
        await callback.message.answer_photo(
            photo=next_front_image_file_id,
            caption=f"{rated_line}\n\n---\n\n{next_front_text}",
            reply_markup=show_answer_kb(next_state_id),
        )
    else:
        await callback.message.answer(
            f"{rated_line}\n\n---\n\n{next_front_text}",
            reply_markup=show_answer_kb(next_state_id),
        )
    await callback.answer()
