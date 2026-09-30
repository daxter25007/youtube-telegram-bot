"""Добавить кэш файлов Telegram.

Revision ID: 20260930_0004
Revises: 20260930_0003
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260930_0004"
down_revision: str | None = "20260930_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "downloads",
        sa.Column("cache_hit", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_table(
        "media_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_media_id", sa.String(length=128), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("requested_option", sa.String(length=64), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=256), nullable=False),
        sa.Column("sent_as_document", sa.Boolean(), nullable=False),
        sa.Column("actual_quality", sa.Integer(), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_cache"),
        sa.UniqueConstraint(
            "source",
            "source_media_id",
            "media_kind",
            "requested_option",
            name="uq_media_cache_identity",
        ),
    )
    op.create_index(
        "ix_media_cache_source_media",
        "media_cache",
        ["source", "source_media_id", "media_kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_cache_source_media", table_name="media_cache")
    op.drop_table("media_cache")
    op.drop_column("downloads", "cache_hit")
