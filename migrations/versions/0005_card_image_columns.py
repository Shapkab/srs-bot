"""add card.front_image_* and card.back_image_* columns

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-18

Optional image attachments on a card. Each side stores BOTH the
Telegram-issued ``file_id`` (cheap re-send) AND a ``sha256`` of the
on-disk byte stream under ``IMAGE_DIR`` (token-rotation insurance —
see ``scripts/reupload_images.py``).

All four columns are nullable; cards without images leave them NULL.
No data backfill — existing rows are text-only cards by definition.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("card", sa.Column("front_image_file_id", sa.String(64), nullable=True))
    op.add_column("card", sa.Column("front_image_sha256", sa.String(64), nullable=True))
    op.add_column("card", sa.Column("back_image_file_id", sa.String(64), nullable=True))
    op.add_column("card", sa.Column("back_image_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("card", "back_image_sha256")
    op.drop_column("card", "back_image_file_id")
    op.drop_column("card", "front_image_sha256")
    op.drop_column("card", "front_image_file_id")
