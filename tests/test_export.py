"""Pure-function test for the /export JSONL builder.

The Telegram-sending shell is not tested; the builder is. With 2 cards
and 3 reviews seeded, we assert:
  * every Card / ReviewState / ReviewLog row appears exactly once
  * row order: card → review_state → review_log
  * review_log rows are sorted by reviewed_at ascending
  * datetimes are ISO 8601 strings; card_json is passed through verbatim
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from src.db import engine as engine_mod
from src.db.crud import add_card, get_or_create_user, persist_review
from src.db.engine import init_db, session_scope
from src.db.models import Rating, ReviewLog, ReviewState
from src.handlers.export import build_export_jsonl
from src.srs.scheduler import apply_review


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    engine_mod._engine = None
    engine_mod._SessionLocal = None
    init_db(tmp_path / "test.db")
    yield


def _seed_two_cards_three_reviews() -> int:
    """Returns the user_id."""
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="card-A", back="back-A")
        add_card(s, user, front="card-B", back="back-B")
        user_id = user.id

    # Review card A twice (Good, Good), then card B once (Hard).
    with session_scope() as s:
        state_a = s.scalar(select(ReviewState).where(ReviewState.card_id == 1))
        assert state_a is not None
        result = apply_review(
            card_json=state_a.card_json,
            prev_due=state_a.due,
            prev_last_review=state_a.last_review,
            rating=Rating.GOOD,
        )
        assert persist_review(s, state_a, Rating.GOOD, result) is True

    # Force card A due again so a 2nd review is possible.
    with session_scope() as s:
        state_a = s.scalar(select(ReviewState).where(ReviewState.card_id == 1))
        state_a.due = datetime.now(UTC) - timedelta(days=1)

    with session_scope() as s:
        state_a = s.scalar(select(ReviewState).where(ReviewState.card_id == 1))
        result = apply_review(
            card_json=state_a.card_json,
            prev_due=state_a.due,
            prev_last_review=state_a.last_review,
            rating=Rating.GOOD,
        )
        assert persist_review(s, state_a, Rating.GOOD, result) is True

    with session_scope() as s:
        state_b = s.scalar(select(ReviewState).where(ReviewState.card_id == 2))
        result = apply_review(
            card_json=state_b.card_json,
            prev_due=state_b.due,
            prev_last_review=state_b.last_review,
            rating=Rating.HARD,
        )
        assert persist_review(s, state_b, Rating.HARD, result) is True

    return user_id


def test_export_contains_all_rows_in_documented_order() -> None:
    user_id = _seed_two_cards_three_reviews()

    with session_scope() as s:
        payload = build_export_jsonl(s, user_id)

    text = payload.decode("utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line]

    types = [r["type"] for r in rows]

    # 2 cards + 2 states + 3 logs = 7 rows total
    assert len(rows) == 7
    assert types.count("card") == 2
    assert types.count("review_state") == 2
    assert types.count("review_log") == 3

    # Documented ordering: card → review_state → review_log
    first_state = types.index("review_state")
    first_log = types.index("review_log")
    assert types[:first_state] == ["card"] * 2
    assert types[first_state:first_log] == ["review_state"] * 2
    assert types[first_log:] == ["review_log"] * 3


def test_export_review_logs_ordered_by_reviewed_at_ascending() -> None:
    user_id = _seed_two_cards_three_reviews()

    with session_scope() as s:
        payload = build_export_jsonl(s, user_id)

    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line]
    logs = [r for r in rows if r["type"] == "review_log"]
    timestamps = [r["reviewed_at"] for r in logs]
    assert timestamps == sorted(timestamps)


def test_export_passes_card_json_through_verbatim_and_uses_iso_datetimes() -> None:
    user_id = _seed_two_cards_three_reviews()

    with session_scope() as s:
        payload = build_export_jsonl(s, user_id)
        # Cross-check against direct DB read.
        states = s.scalars(
            select(ReviewState)
            .where(ReviewState.user_id == user_id)
            .order_by(ReviewState.id.asc())
        ).all()
        db_card_jsons = [st.card_json for st in states]
        log_ids = [
            lg.id
            for lg in s.scalars(
                select(ReviewLog)
                .where(ReviewLog.user_id == user_id)
                .order_by(ReviewLog.reviewed_at.asc(), ReviewLog.id.asc())
            ).all()
        ]

    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line]
    states_out = [r for r in rows if r["type"] == "review_state"]
    logs_out = [r for r in rows if r["type"] == "review_log"]

    assert [r["card_json"] for r in states_out] == db_card_jsons
    assert [r["id"] for r in logs_out] == log_ids

    for r in states_out:
        # ISO 8601 with TZ offset round-trips via fromisoformat.
        parsed = datetime.fromisoformat(r["due"])
        assert parsed.tzinfo is not None
    for r in logs_out:
        parsed = datetime.fromisoformat(r["reviewed_at"])
        assert parsed.tzinfo is not None


def test_export_empty_for_unknown_user() -> None:
    with session_scope() as s:
        payload = build_export_jsonl(s, user_id=999)
    assert payload == b""
