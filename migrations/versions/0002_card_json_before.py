"""add review_log.card_json_before (Phase 3.4 — /undo)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-17

Stores the pre-review FSRS card_json so /undo can restore a ReviewState
to its prior snapshot. Existing rows backfill to empty string.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_log",
        sa.Column(
            "card_json_before",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("review_log", "card_json_before")
