"""add user.reminder_enabled / reminder_threshold / last_reminder_sent_at

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-18

Smart-reminder settings on the user row. An hourly job
(src/jobs/smart_reminder.py) checks the due backlog and nudges the
user when it crosses ``reminder_threshold``, at most once per 24h
(tracked by ``last_reminder_sent_at``).

All three columns get sensible server_defaults so existing user rows
upgrade cleanly: reminders start disabled, threshold 5, never-sent.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "reminder_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "reminder_threshold",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "last_reminder_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "last_reminder_sent_at")
    op.drop_column("user", "reminder_threshold")
    op.drop_column("user", "reminder_enabled")
