"""Inline keyboards for the review flow.

Callback data format: "rv:<action>:<state_id>"
  rv:show:<id>     - reveal the back
  rv:rate:<id>:<r> - submit a rating (r in 1..4 matching Rating enum)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.db.models import Rating


def show_answer_kb(state_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Show answer", callback_data=f"rv:show:{state_id}")]
        ]
    )


def rating_kb(state_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Again", callback_data=f"rv:rate:{state_id}:{Rating.AGAIN.value}"
                ),
                InlineKeyboardButton(
                    text="Hard", callback_data=f"rv:rate:{state_id}:{Rating.HARD.value}"
                ),
                InlineKeyboardButton(
                    text="Good", callback_data=f"rv:rate:{state_id}:{Rating.GOOD.value}"
                ),
                InlineKeyboardButton(
                    text="Easy", callback_data=f"rv:rate:{state_id}:{Rating.EASY.value}"
                ),
            ]
        ]
    )
