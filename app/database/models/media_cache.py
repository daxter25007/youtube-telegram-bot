from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.services.media.models import MediaKind, MediaSource


class MediaCache(TimestampMixin, Base):
    __tablename__ = "media_cache"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_media_id",
            "media_kind",
            "requested_option",
            name="uq_media_cache_identity",
        ),
        Index("ix_media_cache_source_media", "source", "source_media_id", "media_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[MediaSource] = mapped_column(
        Enum(
            MediaSource,
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    source_media_id: Mapped[str] = mapped_column(String(128), nullable=False)
    media_kind: Mapped[MediaKind] = mapped_column(
        Enum(
            MediaKind,
            native_enum=False,
            length=16,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    requested_option: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(256), nullable=False)
    sent_as_document: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actual_quality: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
