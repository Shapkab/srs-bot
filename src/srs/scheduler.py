"""Thin wrapper around py-fsrs.

This module is the *only* place that imports from `fsrs`. The rest of
the codebase deals in plain DB rows and the local Rating/CardState
enums. That keeps the FSRS dependency isolated and easy to swap.

VERIFICATION NOTE (2026-05-17)
------------------------------
Verified against the official py-fsrs README (FSRS 6.3.x):

  https://github.com/open-spaced-repetition/py-fsrs

Confirmed surface used here:
  - `from fsrs import Scheduler, Card, Rating, State`
  - `Scheduler()` with default 21-parameter FSRS 6 weights
  - `card, review_log = scheduler.review_card(card, rating)`  -- 2 args
  - `Card()` constructs a new card due immediately
  - `Card.to_json()` / `Card.from_json(s)` round-trip a card
  - `card.due`, `card.last_review`, `card.state` are public attributes
  - `Rating` integer values 1..4 = Again, Hard, Good, Easy
  - `State` integer values 1..3 = Learning, Review, Relearning
  - py-fsrs uses UTC only

Persistence pattern: we store `Card.to_json()` in `ReviewState.card_json`
and reconstruct via `Card.from_json()` at review time. This is the
library's documented mechanism for "easy database storage."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fsrs import Card as FsrsCard
from fsrs import Rating as FsrsRating
from fsrs import Scheduler
from fsrs import State as FsrsState

from src.db.models import CardState, Rating


# A single Scheduler instance is safe to share — it holds parameters,
# not per-card state. Personalized parameters from fsrs-optimizer
# can be plugged in here later: Scheduler(parameters=(...,))
_scheduler = Scheduler()


# --- Enum mapping ------------------------------------------------------------
# Our DB enums are intentionally separate from fsrs' enums so the DB
# does not break if fsrs renumbers them. Translate at the boundary.

_RATING_TO_FSRS = {
    Rating.AGAIN: FsrsRating.Again,
    Rating.HARD: FsrsRating.Hard,
    Rating.GOOD: FsrsRating.Good,
    Rating.EASY: FsrsRating.Easy,
}

_STATE_TO_FSRS = {
    CardState.LEARNING: FsrsState.Learning,
    CardState.REVIEW: FsrsState.Review,
    CardState.RELEARNING: FsrsState.Relearning,
}

_STATE_FROM_FSRS = {v: k for k, v in _STATE_TO_FSRS.items()}


# --- Public API used by CRUD layer ------------------------------------------


@dataclass
class NewCardState:
    """Fields to populate ReviewState for a brand-new card."""

    card_json: str
    due: datetime
    state: CardState


def new_card_state() -> NewCardState:
    """Initialize FSRS state for a freshly added card.

    Per the README, `Card()` produces a card due immediately in the
    Learning state.
    """
    fsrs_card = FsrsCard()
    return NewCardState(
        card_json=fsrs_card.to_json(),
        due=_ensure_utc(fsrs_card.due),
        state=_STATE_FROM_FSRS[fsrs_card.state],
    )


@dataclass
class ReviewResult:
    """Output of a single review, ready to persist."""

    card_json: str
    due: datetime
    last_review: datetime
    new_state: CardState
    elapsed_days: float
    scheduled_days: float
    state_before: int  # raw int, persisted into ReviewLog.state_before


def apply_review(
    card_json: str,
    prev_due: datetime,
    prev_last_review: datetime | None,
    rating: Rating,
) -> ReviewResult:
    """Run one review through FSRS and return the new state.

    Pure function (no DB writes) so it's trivial to unit-test.

    Inputs are taken from the current ReviewState row. The caller
    persists the result via crud.persist_review().
    """
    fsrs_card = FsrsCard.from_json(card_json)
    state_before_int = int(fsrs_card.state)

    # Documented 2-argument call. The library timestamps the review
    # using its own datetime.now(timezone.utc) internally.
    updated_card, fsrs_log = _scheduler.review_card(fsrs_card, _RATING_TO_FSRS[rating])

    # Use the library's own review_datetime as the canonical timestamp.
    now = _ensure_utc(fsrs_log.review_datetime)

    # Elapsed days since the previous review of this card. Zero on first review.
    elapsed_days = 0.0
    if prev_last_review is not None:
        elapsed_days = (now - _ensure_utc(prev_last_review)).total_seconds() / 86400.0

    # Scheduled interval that was in effect before this review.
    scheduled_days = 0.0
    if prev_last_review is not None:
        scheduled_days = (
            (_ensure_utc(prev_due) - _ensure_utc(prev_last_review)).total_seconds() / 86400.0
        )

    # updated_card.last_review is set by the library to its internal now.
    last_review = _ensure_utc(updated_card.last_review) if updated_card.last_review else now

    return ReviewResult(
        card_json=updated_card.to_json(),
        due=_ensure_utc(updated_card.due),
        last_review=last_review,
        new_state=_STATE_FROM_FSRS[updated_card.state],
        elapsed_days=elapsed_days,
        scheduled_days=scheduled_days,
        state_before=state_before_int,
    )


# --- Helpers -----------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """py-fsrs requires UTC-aware datetimes. SQLite via SQLAlchemy may
    return naive datetimes depending on driver — coerce here.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
