"""Double-tap on a rating button must not produce two reviews.

Simulates the race: two callback handlers each read ReviewState with
``reps == 0`` before either has written. The first to commit wins; the
second's guarded UPDATE finds no row matching ``WHERE reps = 0`` and
no-ops. We assert that ``state.reps == 1`` and exactly one ReviewLog row
ends up in the DB.
"""

from __future__ import annotations

from sqlalchemy import select

from src.db.crud import add_card, get_or_create_user, persist_review
from src.db.engine import session_scope
from src.db.models import Rating, ReviewLog, ReviewState
from src.srs.scheduler import apply_review


def test_double_rate_is_idempotent() -> None:
    # Setup: add one card.
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="a", back="b")

    # Snapshot the state values that *both* concurrent clicks would have
    # read before either committed.
    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state is not None
        state_id = state.id
        stale_card_json = state.card_json
        stale_due = state.due
        stale_last_review = state.last_review
        stale_reps = state.reps
        assert stale_reps == 0

    # First click: real review. Succeeds, commits, reps -> 1.
    with session_scope() as s:
        state = s.get(ReviewState, state_id)
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.GOOD,
        )
        assert persist_review(s, state, Rating.GOOD, result) is True

    # Second click: arrived concurrently with the first, so it carries the
    # stale snapshot (reps=0). Detach to avoid SQLAlchemy flushing our
    # manual reps override back to the DB on commit.
    with session_scope() as s:
        state = s.get(ReviewState, state_id)
        s.expunge(state)
        state.reps = stale_reps  # 0 — simulates what the racing handler saw
        result = apply_review(
            card_json=stale_card_json,
            prev_due=stale_due,
            prev_last_review=stale_last_review,
            rating=Rating.GOOD,
        )
        assert persist_review(s, state, Rating.GOOD, result) is False

    # Exactly one review applied.
    with session_scope() as s:
        state = s.scalar(select(ReviewState))
        assert state is not None
        assert state.reps == 1
        logs = s.scalars(select(ReviewLog)).all()
        assert len(logs) == 1
