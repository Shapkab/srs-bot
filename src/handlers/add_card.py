"""/add (one-shot) and /addm (multi-step FSM) handlers.

  /add front | back              one-shot, fast for power users
  /addm                          prompts for front, then back, then optional tags

The two coexist so the muscle-memory ``/add`` UX is unaffected.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from src.config import Settings
from src.db.crud import add_card, get_or_create_user
from src.db.engine import session_scope

router = Router(name="add_card")


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

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = add_card(s, user, front=front, back=back)
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

    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        card = add_card(s, user, front=front, back=back, tags=tags)
        card_id = card.id

    await message.answer(
        f"Added card #{card_id}. It will appear in your next /review."
    )
