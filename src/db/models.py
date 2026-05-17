"""SQLAlchemy 2.x ORM models for the SRS bot.

Design notes
------------
1. **Four tables**: User, Card, ReviewState, ReviewLog.

2. **Card and ReviewState are separate** even in single-user v1.
   This is the scale-path insurance: when shared/community decks arrive,
   one Card row will have many ReviewState rows (one per user). The
   schema already supports that.

3. **FSRS state is persisted via `card_json`** — the documented py-fsrs
   serialization API. We do NOT manually map each FSRS attribute to a
   column, because:
     * the library documents `Card.to_json()` / `Card.from_json()` as
       the supported persistence path,
     * relying on undocumented kwargs of `Card.__init__` is brittle
       across versions.
   For indexing and querying we denormalize three fields (`due`,
   `last_review`, `state`) that we genuinely need to filter/sort on.
   These are kept in sync with the JSON blob by the scheduler wrapper.

4. **ReviewLog is append-only** and not strictly needed for the review
   loop. It exists so `fsrs-optimizer` can later retrain personalized
   FSRS parameters from your history. Cheap insurance.

5. **All timestamps are UTC**. py-fsrs requires UTC datetimes. Display
   conversion to the user's local TZ happens in the handler layer.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """UTC-aware now."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --- Enums -------------------------------------------------------------------


class CardState(enum.IntEnum):
    """Mirrors fsrs.State, kept as a local enum so the DB does not break
    if a future py-fsrs major renumbers the constants.

    Values match FSRS 6: Learning=1, Review=2, Relearning=3.
    """

    LEARNING = 1
    REVIEW = 2
    RELEARNING = 3


class Rating(enum.IntEnum):
    """Mirrors fsrs.Rating. Persisted in ReviewLog."""

    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


# --- Tables ------------------------------------------------------------------


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    daily_new_limit: Mapped[int] = mapped_column(Integer, default=10)
    daily_review_limit: Mapped[int] = mapped_column(Integer, default=200)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cards: Mapped[list["Card"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    states: Mapped[list["ReviewState"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Card(Base):
    """Content of a flashcard. SRS state lives in ReviewState."""

    __tablename__ = "card"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # In v1 every card belongs to the owner. For shared decks later,
    # make this nullable (NULL = community card) and add `deck_id`.
    owner_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)

    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)

    # Free-form, comma-separated tags. Adequate for v1.
    tags: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # "manual", "import:anki", "llm:article-2026-05", etc.
    source: Mapped[str] = mapped_column(String(64), default="manual")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Soft-delete tombstone. NULL = live; non-NULL = deleted at that time.
    # Kept instead of a hard DELETE so ReviewLog history (used later by
    # fsrs-optimizer) is preserved.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped["User"] = relationship(back_populates="cards")
    state: Mapped["ReviewState"] = relationship(
        back_populates="card", cascade="all, delete-orphan", uselist=False
    )
    logs: Mapped[list["ReviewLog"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )


class ReviewState(Base):
    """Per-user FSRS state for a card.

    `card_json` is the authoritative FSRS state, produced and consumed
    via `fsrs.Card.to_json()` / `fsrs.Card.from_json()`. The other
    fields (`due`, `last_review`, `state`) are denormalized projections
    for indexing/queries. They are written together by the scheduler
    wrapper so they never drift.
    """

    __tablename__ = "review_state"
    __table_args__ = (UniqueConstraint("user_id", "card_id", name="uq_review_state_user_card"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id"), index=True)

    # Authoritative FSRS state. Round-trips via to_json/from_json.
    # NOT NULL: at row creation we serialize a default fsrs.Card().
    card_json: Mapped[str] = mapped_column(Text)

    # --- Denormalized projections (kept in sync with card_json) ------------

    # Indexed because "find next due card" is the primary query.
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_review: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[CardState] = mapped_column(Enum(CardState), default=CardState.LEARNING)

    # --- Aggregate counters (maintained by our code, not FSRS) ------------

    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship(back_populates="states")
    card: Mapped["Card"] = relationship(back_populates="state")


class ReviewLog(Base):
    """Append-only history of every review.

    Required by `fsrs-optimizer` to retrain personalized FSRS parameters
    once you have enough data. Do not mutate; do not delete unless the
    user deletes their data.
    """

    __tablename__ = "review_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)

    rating: Mapped[Rating] = mapped_column(Enum(Rating))
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    # Days since the previous review of this card, at the moment of this
    # review. Zero for the first review.
    elapsed_days: Mapped[float] = mapped_column(Float, default=0.0)

    # Scheduled interval that FSRS had set before this review took place.
    # Zero for the first review.
    scheduled_days: Mapped[float] = mapped_column(Float, default=0.0)

    # The FSRS lifecycle state the card was in at review time. Stored as
    # raw int so an enum rename in py-fsrs doesn't break old rows.
    state_before: Mapped[int] = mapped_column(Integer)

    # Snapshot of ReviewState.card_json BEFORE this review took place. Used
    # by /undo to restore the pre-review FSRS state. Empty string for rows
    # written by code older than this column (see scripts/migrate_001_*).
    card_json_before: Mapped[str] = mapped_column(Text, default="")

    card: Mapped["Card"] = relationship(back_populates="logs")
