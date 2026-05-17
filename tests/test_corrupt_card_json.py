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


def test_apply_review_lets_unrelated_keyerror_propagate() -> None:
    """Phase 8.2: KeyError / TypeError from inside fsrs.Card.from_json
    indicate a library bug (or a structurally unrelated payload that
    happens to parse as JSON), not corrupt user data we can recover
    from. They must NOT be re-wrapped as CorruptCardJsonError — they
    should surface in the structured logs as the unhandled errors they
    are.
    """
    well_formed_but_wrong_shape = '{"completely":"unrelated"}'
    now = datetime.now(UTC)

    # The legacy behaviour was to catch this; the new behaviour is to
    # let it through. Either KeyError or TypeError is acceptable —
    # we just assert it is NOT a CorruptCardJsonError.
    with pytest.raises((KeyError, TypeError)):
        apply_review(
            card_json=well_formed_but_wrong_shape,
            prev_due=now,
            prev_last_review=None,
            rating=Rating.GOOD,
        )


def test_apply_review_raises_corrupt_on_valid_json_with_wrong_value_type() -> None:
    """A ValueError from inside Card.from_json (eg. an unparseable
    datetime string) still goes through the CorruptCardJsonError path —
    that IS recoverable user-data corruption."""
    # A structurally complete-looking but value-broken card_json.
    payload = (
        '{"card_id":1,"state":1,"step":0,"stability":null,"difficulty":null,'
        '"due":"not-a-datetime","last_review":null}'
    )
    now = datetime.now(UTC)
    with pytest.raises(CorruptCardJsonError):
        apply_review(
            card_json=payload,
            prev_due=now,
            prev_last_review=None,
            rating=Rating.GOOD,
        )
