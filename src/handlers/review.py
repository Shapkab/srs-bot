"""/review handler and the two callback handlers (show + rate).

Flow:
  1. /review -> show front of next-due card with a "Show answer" button.
  2. User taps "Show answer" -> we edit the message to show front+back
     with Again/Hard/Good/Easy buttons.
  3. User taps a rating -> we run FSRS, persist, and immediately show
     the next due card (or a "no cards due" message).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from src.config import Settings
from src.db.crud import get_or_create_user, next_due_card, persist_review
from src.db.engine import session_scope
from src.db.models import Card, Rating, ReviewState
from src.keyboards.review import rating_kb, show_answer_kb
from src.srs.scheduler import apply_review

router = Router(name="review")


def _format_front(card: Card) -> str:
    return f"<b>{card.front}</b>"


def _format_front_back(card: Card) -> str:
    return f"<b>{card.front}</b>\n\n{card.back}"


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
        # Load the Card while still in session.
        state_id = state.id
        front_text = _format_front(state.card)

    await message.answer(front_text, reply_markup=show_answer_kb(state_id))


@router.callback_query(F.data.startswith("rv:show:"))
async def cb_show_answer(callback: CallbackQuery) -> None:
    state_id = int(callback.data.split(":")[2])

    with session_scope() as s:
        state = s.scalar(select(ReviewState).where(ReviewState.id == state_id))
        if state is None:
            await callback.answer("Card not found.", show_alert=True)
            return
        text = _format_front_back(state.card)

    # edit_text raises if the message is unchanged; safe here because
    # we're appending the back, which is new content.
    await callback.message.edit_text(text, reply_markup=rating_kb(state_id))
    await callback.answer()


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

        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=rating,
        )
        persist_review(s, state, rating, result)

        # Look up the next due card in the same transaction so the
        # user sees an uninterrupted stream.
        user = state.user
        next_state = next_due_card(s, user)
        if next_state is not None:
            next_state_id = next_state.id
            next_front = _format_front(next_state.card)
        else:
            next_state_id = None
            next_front = None

    # Replace the current message with a confirmation + next card or end-of-session note.
    if next_state_id is not None:
        await callback.message.edit_text(
            f"Rated: <b>{rating.name.title()}</b>\n\n---\n\n{next_front}",
            reply_markup=show_answer_kb(next_state_id),
        )
    else:
        await callback.message.edit_text(
            f"Rated: <b>{rating.name.title()}</b>\n\nNothing more due. Nice work."
        )
    await callback.answer()
