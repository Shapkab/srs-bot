"""apply_review must raise a typed exception when card_json is unparseable,
not silently corrupt downstream state by feeding garbage to FSRS.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.models import Rating
from src.srs.scheduler import CorruptCardJsonError, apply_review


def test_apply_review_raises_corrupt_card_json_error_on_truncated_json() -> None:
    bad_json = '{"bad":"json"'  # missing closing brace — JSONDecodeError
    now = datetime.now(UTC)

    with pytest.raises(CorruptCardJsonError) as ei:
        apply_review(
            card_json=bad_json,
            prev_due=now,
            prev_last_review=None,
            rating=Rating.GOOD,
        )

    assert ei.value.state_id is None  # scheduler layer doesn't know it
    assert ei.value.original is not None
    assert isinstance(ei.value.original, (ValueError, KeyError, TypeError))


def test_apply_review_raises_on_well_formed_json_missing_required_fields() -> None:
    # Parses fine as JSON but lacks the fields fsrs.Card.from_json needs.
    bad_json = '{"completely":"unrelated"}'
    now = datetime.now(UTC)

    with pytest.raises(CorruptCardJsonError):
        apply_review(
            card_json=bad_json,
            prev_due=now,
            prev_last_review=None,
            rating=Rating.GOOD,
        )
