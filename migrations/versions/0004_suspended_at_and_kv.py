"""add review_state.suspended_at and create kv table (Phase 4.1, 4.4)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-17

* review_state.suspended_at — auto-set by persist_review when lapses
  cross LEECH_LAPSE_THRESHOLD; suspended states are excluded from
  /review and due_count.
* kv — one-row-per-key app-scalar store used so far for
  ``daily_reminder.last_fired``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_state",
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "kv",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("kv")
    op.drop_column("review_state", "suspended_at")
