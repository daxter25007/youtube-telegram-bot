"""Добавить backend загрузки.

Revision ID: 20260930_0003
Revises: 20260929_0002
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260930_0003"
down_revision: str | None = "20260929_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("downloads", sa.Column("backend", sa.String(length=16), nullable=True))
    op.execute(
        sa.text(
            "UPDATE downloads SET backend = 'ytdlp' WHERE status = 'completed' AND backend IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("downloads", "backend")
