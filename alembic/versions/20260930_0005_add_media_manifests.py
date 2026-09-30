"""Добавить манифесты медиапубликаций.

Revision ID: 20260930_0005
Revises: 20260930_0004
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260930_0005"
down_revision: str | None = "20260930_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_manifests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_media_id", sa.String(length=128), nullable=False),
        sa.Column("parts_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_manifests"),
        sa.UniqueConstraint(
            "source",
            "source_media_id",
            name="uq_media_manifests_source",
        ),
    )


def downgrade() -> None:
    op.drop_table("media_manifests")
