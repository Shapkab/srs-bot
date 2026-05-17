"""Pure-function tests for ``compute_stats``.

We seed deterministic Card / ReviewState / ReviewLog rows directly and
assert the numbers come out as expected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.db import engine as engine_mod
from src.db.crud import add_card, get_or_create_user
from src.db.engine import init_db, session_scope
from src.db.models import CardState, Rating, ReviewLog, ReviewState
from src.handlers.stats import compute_stats


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    engine_mod._engine = None
    engine_mod._SessionLocal = None
    init_db(tmp_path / "test.db")
    yield


def _seed_empty_user() -> int:
    with session_scope() as s:
        u = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        return u.id


def test_compute_stats_empty_user() -> None:
    user_id = _seed_empty_user()
    with session_scope() as s:
        stats = compute_stats(s, user_id)
    assert stats.total_cards == 0
    assert stats.due_now == 0
    assert stats.learning == stats.review == stats.relearning == 0
    assert stats.reviews_last_7d == 0
    assert stats.retention_30d is None


def test_compute_stats_counts_live_cards_and_excludes_deleted() -> None:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="a", back="a")
        add_card(s, user, front="b", back="b")
        deleted_card = add_card(s, user, front="c", back="c")
        deleted_card.deleted_at = datetime.now(UTC)
        user_id = user.id

    with session_scope() as s:
        stats = compute_stats(s, user_id)
    # 3 added, 1 soft-deleted → 2 live.
    assert stats.total_cards == 2
    # All three (well, 2 live) are LEARNING and due now.
    assert stats.learning == 2
    assert stats.review == 0
    assert stats.relearning == 0
    assert stats.due_now == 2


def test_compute_stats_excludes_suspended_from_state_counts() -> None:
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="a", back="a")
        add_card(s, user, front="b", back="b")
        suspended = s.query(ReviewState).first()  # type: ignore[no-untyped-call]
        suspended.suspended_at = datetime.now(UTC)
        user_id = user.id

    with session_scope() as s:
        stats = compute_stats(s, user_id)
    # total_cards counts Cards (not states), so both still appear.
    assert stats.total_cards == 2
    # state count excludes the suspended one.
    assert stats.learning == 1


def test_compute_stats_review_window_and_retention() -> None:
    """Manually seed ReviewLog rows over a known timeline so we control
    the math, then assert the 7-day / 30-day windows and retention rate.
    """
    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        card = add_card(s, user, front="x", back="y")
        user_id = user.id
        card_id = card.id

    now = datetime.now(UTC)
    log_recipe = [
        # (days_ago, rating)
        (1, Rating.GOOD),
        (2, Rating.GOOD),
        (3, Rating.AGAIN),
        (6, Rating.EASY),
        (8, Rating.HARD),        # outside 7d window, inside 30d
        (15, Rating.GOOD),       # inside 30d
        (29, Rating.AGAIN),      # inside 30d (edge)
        (45, Rating.GOOD),       # outside 30d
    ]
    with session_scope() as s:
        for days_ago, rating in log_recipe:
            s.add(
                ReviewLog(
                    card_id=card_id,
                    user_id=user_id,
                    rating=rating,
                    reviewed_at=now - timedelta(days=days_ago),
                    state_before=int(CardState.LEARNING),
                )
            )

    with session_scope() as s:
        stats = compute_stats(s, user_id, now=now)

    # Last 7 days: days_ago in {1,2,3,6} → 4 rows
    assert stats.reviews_last_7d == 4
    # Last 30 days: days_ago <= 29 → 7 rows
    # Good+Easy among those 7: (1,GOOD),(2,GOOD),(6,EASY),(15,GOOD) = 4
    # Total 30d = 7, retention = 4/7
    assert stats.retention_30d == pytest.approx(4 / 7, rel=1e-6)


def test_compute_stats_retention_none_when_no_reviews() -> None:
    user_id = _seed_empty_user()
    with session_scope() as s:
        stats = compute_stats(s, user_id)
    assert stats.retention_30d is None
