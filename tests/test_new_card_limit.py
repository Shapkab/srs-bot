"""``daily_new_limit`` caps the number of *new* cards introduced per
UTC day. Already-touched cards (``last_review IS NOT NULL``) are not
capped — those are owed work.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.db.crud import add_card, get_or_create_user, next_due_card, persist_review
from src.db.engine import session_scope
from src.db.models import Rating, ReviewState
from src.srs.scheduler import apply_review


def _seed(user_telegram_id: int, daily_new_limit: int, n_cards: int) -> int:
    with session_scope() as s:
        user = get_or_create_user(
            s, telegram_id=user_telegram_id, username="t", tz="UTC"
        )
        user.daily_new_limit = daily_new_limit
        for i in range(n_cards):
            add_card(s, user, front=f"f{i}", back=f"b{i}")
        return user.id


def _consume_new_cards(user_telegram_id: int) -> int:
    """Pop next_due_card and rate Good until None. Returns count consumed."""
    count = 0
    while True:
        with session_scope() as s:
            user = get_or_create_user(
                s, telegram_id=user_telegram_id, username="t", tz="UTC"
            )
            state = next_due_card(s, user)
            if state is None:
                break
            result = apply_review(
                card_json=state.card_json,
                prev_due=state.due,
                prev_last_review=state.last_review,
                rating=Rating.GOOD,
            )
            assert persist_review(s, state, Rating.GOOD, result) is True
        count += 1
    return count


def test_only_daily_new_limit_new_cards_surface_today() -> None:
    _seed(user_telegram_id=1, daily_new_limit=2, n_cards=5)

    # All 5 cards are "new" (LEARNING, no last_review). Cap is 2.
    consumed = _consume_new_cards(user_telegram_id=1)
    assert consumed == 2

    # The remaining three cards are still in the DB but excluded from the queue
    # because today's cap is hit.
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        assert next_due_card(s, user) is None
        # And no states have been suspended/deleted.
        states = s.scalars(select(ReviewState)).all()
        assert len(states) == 5
        never_reviewed = [st for st in states if st.last_review is None]
        assert len(never_reviewed) == 3


def test_third_new_card_surfaces_tomorrow() -> None:
    _seed(user_telegram_id=1, daily_new_limit=2, n_cards=5)

    # Today: hit the cap of 2 new cards.
    assert _consume_new_cards(user_telegram_id=1) == 2

    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        assert next_due_card(s, user) is None  # cap hit, no more new today

    # "Tomorrow": backdate today's ReviewLog rows by a day so the new-card
    # count resets. (Live state.due values for the 3 untouched cards are
    # already in the past from when they were added.)
    from src.db.models import ReviewLog as RL

    with session_scope() as s:
        for log in s.scalars(select(RL)).all():
            log.reviewed_at = log.reviewed_at - timedelta(days=1)

    # A never-reviewed card must now surface — that's the "third" card.
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        state = next_due_card(s, user)
        assert state is not None
        assert state.last_review is None  # a brand-new card


def test_already_reviewed_cards_are_not_capped() -> None:
    _seed(user_telegram_id=1, daily_new_limit=1, n_cards=2)

    # Card 1: introduce (counts as new today, hits the cap).
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        state = next_due_card(s, user)
        assert state is not None
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.GOOD,
        )
        assert persist_review(s, state, Rating.GOOD, result) is True

    # Card 2 (still new) must NOT surface — cap is 1.
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        assert next_due_card(s, user) is None

    # Force card 1 to be due again. It already has last_review, so it must
    # surface despite the new-card cap being hit.
    with session_scope() as s:
        state = s.scalar(select(ReviewState).where(ReviewState.reps == 1))
        state.due = datetime.now(UTC) - timedelta(seconds=1)

    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        state = next_due_card(s, user)
        assert state is not None
        assert state.last_review is not None  # not a new card
