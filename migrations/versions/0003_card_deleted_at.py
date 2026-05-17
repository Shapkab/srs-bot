"""add card.deleted_at (Phase 3.3 — soft-delete)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17

Soft-delete tombstone on Card. NULL = live; non-NULL = deleted at that
time. Filtered out by /review, /cards, and due_count; logs preserved
for fsrs-optimizer.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "card",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("card", "deleted_at")
