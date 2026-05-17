"""Smoke tests for the fsrs-optimizer entry point.

The actual optimizer wiring is exercised by hand once enough review
history exists. Here we just check that the script:

  * imports cleanly
  * exits gracefully on empty DB / bad CLI / missing optimizer API
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.optimize import main


def test_optimize_main_prints_not_enough_data_on_empty_db(
    fresh_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main([__file__, str(fresh_db)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "not enough data" in captured.out.lower()


def test_optimize_main_errors_on_missing_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main([__file__, str(tmp_path / "does-not-exist.db")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "no such file" in captured.err.lower()


def test_optimize_main_usage(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([__file__])
    assert rc == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower()


def test_optimize_main_handles_missing_optimize_with_db(
    fresh_db: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Phase 8.7: a version of fsrs-optimizer without ``optimize_with_db``
    must produce a clear, actionable message — not an AttributeError
    traceback.
    """
    # Seed enough fake review_log rows that the "not enough data" guard
    # is bypassed and the optimizer path is reached.
    from datetime import UTC, datetime

    from sqlalchemy import select

    from scripts.optimize import MIN_REVIEWS
    from src.db.crud import add_card, get_or_create_user
    from src.db.engine import session_scope
    from src.db.models import Card, Rating, ReviewLog

    with session_scope() as s:
        user = get_or_create_user(s, telegram_id=1, username="t", tz="UTC")
        add_card(s, user, front="x", back="y")
        card_id = s.scalar(select(Card)).id
        user_id = user.id

    # Inflate to MIN_REVIEWS+ ReviewLog rows so the "not enough data"
    # branch is bypassed and the missing-optimizer-API path is reached.
    now = datetime.now(UTC)
    with session_scope() as s:
        for _ in range(MIN_REVIEWS):
            s.add(
                ReviewLog(
                    card_id=card_id,
                    user_id=user_id,
                    rating=Rating.GOOD,
                    reviewed_at=now,
                    state_before=1,
                )
            )

    # Inject a fake fsrs_optimizer module whose Optimizer instance lacks
    # ``optimize_with_db``. The script's lazy import will pick it up.
    fake = types.ModuleType("fsrs_optimizer")

    class _Stub:
        pass

    fake.Optimizer = _Stub  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"fsrs_optimizer": fake}):
        rc = main([__file__, str(fresh_db)])

    assert rc == 1
    captured = capsys.readouterr()
    msg = captured.err.lower()
    assert "optimize_with_db" in msg
    assert "stub" in msg or "update" in msg
    # No traceback in stderr.
    assert "traceback" not in msg
