"""/stats — summary numbers for the owner.

  Total cards (live, not soft-deleted)
  Due now
  Learning / Review / Relearning counts (live, not suspended)
  Reviews in the last 7 days
  Retention rate over the last 30 days  =  (Good + Easy) / Total

No schema changes. The pure ``compute_stats`` function is factored out
of the handler so it is testable without aiogram.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.config import Settings
from src.db.crud import due_count, get_or_create_user
from src.db.engine import session_scope
from src.db.models import Card, CardState, Rating, ReviewLog, ReviewState, User

router = Router(name="stats")


@dataclass
class Stats:
    total_cards: int
    due_now: int
    learning: int
    review: int
    relearning: int
    reviews_last_7d: int
    # None when there are no reviews in the last 30 days — we report N/A
    # rather than 0% so the user isn't told they've forgotten everything.
    retention_30d: float | None


def _count_state(s: Session, user_id: int, target_state: CardState) -> int:
    stmt = (
        select(func.count())
        .select_from(ReviewState)
        .join(Card, ReviewState.card_id == Card.id)
        .where(
            ReviewState.user_id == user_id,
            ReviewState.state == target_state,
            ReviewState.suspended_at.is_(None),
            Card.deleted_at.is_(None),
        )
    )
    return s.scalar(stmt) or 0


def compute_stats(s: Session, user_id: int, now: datetime | None = None) -> Stats:
    now = now or datetime.now(UTC)
    cutoff_7d = now - timedelta(days=7)
    cutoff_30d = now - timedelta(days=30)

    total_cards = s.scalar(
        select(func.count())
        .select_from(Card)
        .where(Card.owner_id == user_id, Card.deleted_at.is_(None))
    ) or 0

    user = s.get(User, user_id)
    due_now = due_count(s, user, now) if user is not None else 0

    learning = _count_state(s, user_id, CardState.LEARNING)
    review = _count_state(s, user_id, CardState.REVIEW)
    relearning = _count_state(s, user_id, CardState.RELEARNING)

    reviews_last_7d = s.scalar(
        select(func.count())
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= cutoff_7d,
        )
    ) or 0

    total_30d = s.scalar(
        select(func.count())
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= cutoff_30d,
        )
    ) or 0

    retention_30d: float | None
    if total_30d == 0:
        retention_30d = None
    else:
        good_easy_30d = s.scalar(
            select(func.count())
            .select_from(ReviewLog)
            .where(
                ReviewLog.user_id == user_id,
                ReviewLog.reviewed_at >= cutoff_30d,
                ReviewLog.rating.in_([Rating.GOOD, Rating.EASY]),
            )
        ) or 0
        retention_30d = good_easy_30d / total_30d

    return Stats(
        total_cards=total_cards,
        due_now=due_now,
        learning=learning,
        review=review,
        relearning=relearning,
        reviews_last_7d=reviews_last_7d,
        retention_30d=retention_30d,
    )


def _format(stats: Stats) -> str:
    retention = (
        f"{stats.retention_30d * 100:.1f}%"
        if stats.retention_30d is not None
        else "N/A"
    )
    return (
        "<b>Stats</b>\n"
        f"Total cards: {stats.total_cards}\n"
        f"Due now: {stats.due_now}\n"
        f"Learning / Review / Relearning: "
        f"{stats.learning} / {stats.review} / {stats.relearning}\n"
        f"Reviews (last 7d): {stats.reviews_last_7d}\n"
        f"Retention (last 30d): {retention}"
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, settings: Settings) -> None:
    with session_scope() as s:
        user = get_or_create_user(
            s,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            tz=settings.timezone,
        )
        stats = compute_stats(s, user.id)
    await message.answer(_format(stats))
