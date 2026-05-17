"""Small CRUD layer between handlers and the DB.

Keeps handlers free of SQLAlchemy syntax and gives us a single place
to add caching / metrics later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.db.models import Card, Rating, ReviewLog, ReviewState, User
from src.srs.scheduler import ReviewResult, new_card_state


def get_or_create_user(s: Session, telegram_id: int, username: Optional[str], tz: str) -> User:
    user = s.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=username, timezone=tz)
        s.add(user)
        s.flush()
    return user


def add_card(
    s: Session,
    user: User,
    front: str,
    back: str,
    tags: Optional[str] = None,
    source: str = "manual",
) -> Card:
    card = Card(owner_id=user.id, front=front, back=back, tags=tags, source=source)
    s.add(card)
    s.flush()

    init = new_card_state()
    state = ReviewState(
        user_id=user.id,
        card_id=card.id,
        card_json=init.card_json,
        due=init.due,
        state=init.state,
    )
    s.add(state)
    s.flush()
    return card


def next_due_card(s: Session, user: User, now: datetime | None = None) -> Optional[ReviewState]:
    """Return the next ReviewState whose card is due, oldest-due first."""
    now = now or datetime.now(timezone.utc)
    stmt = (
        select(ReviewState)
        .where(ReviewState.user_id == user.id, ReviewState.due <= now)
        .order_by(ReviewState.due.asc())
        .limit(1)
    )
    return s.scalar(stmt)


def due_count(s: Session, user: User, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    stmt = select(func.count()).select_from(ReviewState).where(
        ReviewState.user_id == user.id, ReviewState.due <= now
    )
    return s.scalar(stmt) or 0


def persist_review(
    s: Session, state: ReviewState, rating: Rating, result: ReviewResult
) -> bool:
    """Apply a ReviewResult to the ReviewState row and append a ReviewLog row.

    Guarded by an optimistic-concurrency check on ``reps``: the UPDATE only
    fires if the row's ``reps`` still matches what the caller saw. Returns
    True on success and False when another concurrent click already
    advanced the row (rowcount == 0).

    Caller already opened a session_scope(); we just issue the update and add.
    """
    expected_reps = state.reps
    new_reps = expected_reps + 1
    new_lapses = state.lapses + (1 if rating == Rating.AGAIN else 0)

    res = s.execute(
        update(ReviewState)
        .where(ReviewState.id == state.id, ReviewState.reps == expected_reps)
        .values(
            card_json=result.card_json,
            due=result.due,
            last_review=result.last_review,
            state=result.new_state,
            reps=new_reps,
            lapses=new_lapses,
        )
    )
    if res.rowcount != 1:
        return False

    # Sync the in-memory ORM instance so callers (and tests) see the
    # post-update values without SQLAlchemy emitting a second UPDATE on flush.
    if state in s:
        s.refresh(state)

    s.add(
        ReviewLog(
            card_id=state.card_id,
            user_id=state.user_id,
            rating=rating,
            reviewed_at=result.last_review,
            elapsed_days=result.elapsed_days,
            scheduled_days=result.scheduled_days,
            state_before=result.state_before,
        )
    )
    return True
