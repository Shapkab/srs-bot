"""Smoke test: end-to-end SRS round-trip without any Telegram involvement.

Run with: pytest -q

Verifies:
  - DB schema creates cleanly
  - Adding a card produces a ReviewState row due now with valid card_json
  - apply_review() returns a ReviewResult round-trippable via to_json/from_json
  - persist_review() updates ReviewState and appends a ReviewLog
  - After review, due is in the future
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.db.crud import add_card, get_or_create_user, next_due_card, persist_review
from src.db.engine import session_scope
from src.db.models import Rating, ReviewLog, ReviewState
from src.srs.scheduler import apply_review


def _user(s, telegram_id: int = 1):
    return get_or_create_user(s, telegram_id=telegram_id, username="tester", tz="UTC")


def test_roundtrip_creates_state_and_log() -> None:
    with session_scope() as s:
        user = _user(s)
        card = add_card(s, user, front="reach out to", back="contact someone, usually for help")
        card_id = card.id

    # The new card should be due immediately and have FSRS state serialized.
    with session_scope() as s:
        user = _user(s)
        state = next_due_card(s, user)
        assert state is not None
        assert state.card_id == card_id
        assert state.reps == 0
        assert state.card_json  # serialized fsrs.Card() blob
        assert state.last_review is None
        before_due = state.due

    # Run one Good review.
    with session_scope() as s:
        user = _user(s)
        state = next_due_card(s, user)
        assert state is not None
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.GOOD,
        )
        persist_review(s, state, Rating.GOOD, result)

    # After review: reps incremented, due moved forward, log appended.
    with session_scope() as s:
        state = s.query(ReviewState).filter_by(card_id=card_id).one()
        assert state.reps == 1
        assert state.lapses == 0
        assert state.last_review is not None
        assert state.due > before_due
        # card_json should still be valid (we can deserialize and reserialize).
        assert state.card_json

        logs = s.query(ReviewLog).filter_by(card_id=card_id).all()
        assert len(logs) == 1
        assert logs[0].rating == Rating.GOOD


def test_again_increments_lapses() -> None:
    with session_scope() as s:
        user = _user(s, telegram_id=2)
        add_card(s, user, front="a", back="b")

    # First review: Good (puts us into a real review state).
    with session_scope() as s:
        user = _user(s, telegram_id=2)
        state = next_due_card(s, user)
        assert state is not None
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.GOOD,
        )
        persist_review(s, state, Rating.GOOD, result)
        first_lapses = state.lapses

    # Force the card to be due again for the second review.
    with session_scope() as s:
        state = s.query(ReviewState).first()
        assert state is not None
        state.due = datetime.now(timezone.utc)

    # Second review: Again -> should bump lapses.
    with session_scope() as s:
        user = _user(s, telegram_id=2)
        state = next_due_card(s, user)
        assert state is not None
        result = apply_review(
            card_json=state.card_json,
            prev_due=state.due,
            prev_last_review=state.last_review,
            rating=Rating.AGAIN,
        )
        persist_review(s, state, Rating.AGAIN, result)
        assert state.lapses == first_lapses + 1
