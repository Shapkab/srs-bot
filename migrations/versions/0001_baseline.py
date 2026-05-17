"""baseline (pre-Phase-3 schema)

Revision ID: 0001
Revises:
Create Date: 2026-05-17

Creates the four original tables (user, card, review_state, review_log)
in the shape they had at Phase 0, before any column adds. Subsequent
revisions add columns / new tables one piece at a time so a pre-Phase-3
DB can be brought to head via plain ``alembic upgrade head`` (after
``alembic stamp 0001`` to register the current state).

Fresh installs simply run ``alembic upgrade head`` from scratch.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("daily_new_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("daily_review_limit", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_user_telegram_id", "user", ["telegram_id"], unique=True)

    op.create_table(
        "card",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("tags", sa.String(255), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_card_owner_id", "card", ["owner_id"])

    op.create_table(
        "review_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("card.id"), nullable=False),
        sa.Column("card_json", sa.Text(), nullable=False),
        sa.Column("due", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_review", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "state",
            sa.Enum("LEARNING", "REVIEW", "RELEARNING", name="cardstate"),
            nullable=False,
        ),
        sa.Column("reps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lapses", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "card_id", name="uq_review_state_user_card"),
    )
    op.create_index("ix_review_state_user_id", "review_state", ["user_id"])
    op.create_index("ix_review_state_card_id", "review_state", ["card_id"])
    op.create_index("ix_review_state_due", "review_state", ["due"])

    op.create_table(
        "review_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("card.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "rating",
            sa.Enum("AGAIN", "HARD", "GOOD", "EASY", name="rating"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("scheduled_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("state_before", sa.Integer(), nullable=False),
    )
    op.create_index("ix_review_log_card_id", "review_log", ["card_id"])
    op.create_index("ix_review_log_user_id", "review_log", ["user_id"])
    op.create_index("ix_review_log_reviewed_at", "review_log", ["reviewed_at"])


def downgrade() -> None:
    op.drop_table("review_log")
    op.drop_table("review_state")
    op.drop_table("card")
    op.drop_table("user")
