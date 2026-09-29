"""Обобщить источники медиа.

Revision ID: 20260929_0002
Revises: 20260928_0001
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260929_0002"
down_revision: str | None = "20260928_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("downloads", "youtube_url", new_column_name="source_url")
    op.alter_column("downloads", "youtube_video_id", new_column_name="source_media_id")
    op.alter_column("download_requests", "youtube_url", new_column_name="source_url")
    op.alter_column("download_requests", "youtube_video_id", new_column_name="source_media_id")

    for table in ("downloads", "download_requests"):
        op.alter_column(
            table,
            "source_media_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
        )

    for table in ("downloads", "download_requests"):
        op.add_column(table, sa.Column("source", sa.String(length=32), nullable=True))
        op.add_column(table, sa.Column("media_kind", sa.String(length=16), nullable=True))
        if table == "downloads":
            op.execute(sa.text("UPDATE downloads SET source = 'youtube' WHERE source IS NULL"))
            op.execute(
                sa.text("UPDATE downloads SET media_kind = 'video' WHERE media_kind IS NULL")
            )
        else:
            op.execute(
                sa.text("UPDATE download_requests SET source = 'youtube' WHERE source IS NULL")
            )
            op.execute(
                sa.text(
                    "UPDATE download_requests SET media_kind = 'video' WHERE media_kind IS NULL"
                )
            )
        op.alter_column(table, "source", existing_type=sa.String(length=32), nullable=False)
        op.alter_column(table, "media_kind", existing_type=sa.String(length=16), nullable=False)

    op.add_column("downloads", sa.Column("requested_option", sa.String(length=64), nullable=True))
    op.execute(
        sa.text(
            "UPDATE downloads SET requested_option = 'video_' || "
            "CAST(requested_quality AS VARCHAR) WHERE requested_option IS NULL"
        )
    )
    op.alter_column(
        "downloads",
        "requested_option",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column("downloads", "requested_quality", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE downloads SET requested_quality = COALESCE(actual_quality, 0) "
            "WHERE requested_quality IS NULL"
        )
    )
    op.alter_column("downloads", "requested_quality", existing_type=sa.Integer(), nullable=False)
    op.drop_column("downloads", "requested_option")
    for table in ("download_requests", "downloads"):
        op.drop_column(table, "media_kind")
        op.drop_column(table, "source")

    op.alter_column("downloads", "source_url", new_column_name="youtube_url")
    op.alter_column("downloads", "source_media_id", new_column_name="youtube_video_id")
    op.alter_column("download_requests", "source_url", new_column_name="youtube_url")
    op.alter_column("download_requests", "source_media_id", new_column_name="youtube_video_id")
