"""Small CRUD layer between handlers and the DB.

Keeps handlers free of SQLAlchemy syntax and gives us a single place
to add caching / metrics later.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.db.models import Card, CardState, Rating, ReviewLog, ReviewState, User
from src.srs.scheduler import ReviewResult, new_card_state

# Default Anki leech threshold. Surface that hits this many AGAINs gets
# suspended out of the review queue until the user un-suspends it (a future
# feature; for now, leeches just stay out of /review).
LEECH_LAPSE_THRESHOLD = 8


def get_or_create_user(s: Session, telegram_id: int, username: str | None, tz: str) -> User:
    user = s.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, username=username, timezone=tz)
        s.add(user)
        s.flush()
    return user


def add_card(
    s: Session,
    user: User,
    front: str,
    back: str,
    tags: str | None = None,
    source: str = "manual",
    front_image_file_id: str | None = None,
    front_image_sha256: str | None = None,
    back_image_file_id: str | None = None,
    back_image_sha256: str | None = None,
) -> Card:
    card = Card(
        owner_id=user.id,
        front=front,
        back=back,
        tags=tags,
        source=source,
        front_image_file_id=front_image_file_id,
        front_image_sha256=front_image_sha256,
        back_image_file_id=back_image_file_id,
        back_image_sha256=back_image_sha256,
    )
    s.add(card)
    s.flush()

    init = new_card_state()
    state = ReviewState(
        user_id=user.id,
        card_id=card.id,
        card_json=init.card_json,
        due=init.due,
        state=init.state,
    )
    s.add(state)
    s.flush()
    return card


def _today_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date(), time(0, 0, tzinfo=UTC))


def _todays_new_card_count(s: Session, user: User, now: datetime) -> int:
    """Count today's reviews of NEW cards (state_before == LEARNING)."""
    stmt = (
        select(func.count())
        .select_from(ReviewLog)
        .where(
            ReviewLog.user_id == user.id,
            ReviewLog.state_before == int(CardState.LEARNING),
            ReviewLog.reviewed_at >= _today_utc_midnight(now),
        )
    )
    return s.scalar(stmt) or 0


def next_due_card(s: Session, user: User, now: datetime | None = None) -> ReviewState | None:
    """Return the next ReviewState whose card is due, oldest-due first.

    Excluded:
      * soft-deleted cards (``Card.deleted_at IS NOT NULL``)
      * suspended states (``ReviewState.suspended_at IS NOT NULL``)
      * never-reviewed (new) cards once today's count of new-card reviews
        has reached ``User.daily_new_limit``

    Reviews of cards the user has already touched are not capped — those
    are owed work, not new starts.
    """
    now = now or datetime.now(UTC)
    stmt = (
        select(ReviewState)
        .join(Card, ReviewState.card_id == Card.id)
        .where(
            ReviewState.user_id == user.id,
            ReviewState.due <= now,
            ReviewState.suspended_at.is_(None),
            Card.deleted_at.is_(None),
        )
    )
    if _todays_new_card_count(s, user, now) >= user.daily_new_limit:
        # No more new cards today — show only states with prior reviews.
        stmt = stmt.where(ReviewState.last_review.is_not(None))

    return s.scalar(stmt.order_by(ReviewState.due.asc()).limit(1))


def due_count(s: Session, user: User, now: datetime | None = None) -> int:
    """Count of cards the user could review right now.

    Mirrors the exclusions of ``next_due_card`` (soft-deleted, suspended).
    Does NOT apply the daily_new_limit cap — that's a queue-shaping rule
    for /review, not a count of work owed.
    """
    now = now or datetime.now(UTC)
    stmt = (
        select(func.count())
        .select_from(ReviewState)
        .join(Card, ReviewState.card_id == Card.id)
        .where(
            ReviewState.user_id == user.id,
            ReviewState.due <= now,
            ReviewState.suspended_at.is_(None),
            Card.deleted_at.is_(None),
        )
    )
    return s.scalar(stmt) or 0


def persist_review(
    s: Session, state: ReviewState, rating: Rating, result: ReviewResult
) -> bool:
    """Apply a ReviewResult to the ReviewState row and append a ReviewLog row.

    Guarded by an optimistic-concurrency check on ``reps``: the UPDATE only
    fires if the row's ``reps`` still matches what the caller saw. Returns
    True on success and False when another concurrent click already
    advanced the row (rowcount == 0).

    Caller already opened a session_scope(); we just issue the update and add.
    """
    expected_reps = state.reps
    # Snapshot card_json before the update so /undo can restore it.
    card_json_before = state.card_json
    new_reps = expected_reps + 1
    new_lapses = state.lapses + (1 if rating == Rating.AGAIN else 0)
    # Auto-suspend when lapses cross the leech threshold. Done atomically
    # with the row update so a concurrent /review never sees a leech still
    # in the queue.
    new_suspended_at = (
        result.last_review if new_lapses >= LEECH_LAPSE_THRESHOLD else state.suspended_at
    )

    res = s.execute(
        update(ReviewState)
        .where(ReviewState.id == state.id, ReviewState.reps == expected_reps)
        .values(
            card_json=result.card_json,
            due=result.due,
            last_review=result.last_review,
            state=result.new_state,
            reps=new_reps,
            lapses=new_lapses,
            suspended_at=new_suspended_at,
        )
    )
    if res.rowcount != 1:
        return False

    # Sync the in-memory ORM instance so callers (and tests) see the
    # post-update values without SQLAlchemy emitting a second UPDATE on flush.
    if state in s:
        s.refresh(state)

    s.add(
        ReviewLog(
            card_id=state.card_id,
            user_id=state.user_id,
            rating=rating,
            reviewed_at=result.last_review,
            elapsed_days=result.elapsed_days,
            scheduled_days=result.scheduled_days,
            state_before=result.state_before,
            card_json_before=card_json_before,
        )
    )
    return True
