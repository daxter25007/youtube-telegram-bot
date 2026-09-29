from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin
from app.services.media.models import MediaKind, MediaSource


class DownloadStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class Download(TimestampMixin, Base):
    __tablename__ = "downloads"
    __table_args__ = (
        Index("ix_downloads_user_id", "user_id"),
        Index("ix_downloads_created_at", "created_at"),
        Index("ix_downloads_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[MediaSource] = mapped_column(
        Enum(
            MediaSource,
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    media_kind: Mapped[MediaKind] = mapped_column(
        Enum(
            MediaKind,
            native_enum=False,
            length=16,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    source_media_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    video_title: Mapped[str] = mapped_column(String(512), nullable=False)
    requested_option: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_quality: Mapped[int | None] = mapped_column(Integer)
    actual_quality: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[DownloadStatus] = mapped_column(
        Enum(
            DownloadStatus,
            native_enum=False,
            length=32,
            values_callable=lambda members: [member.value for member in members],
        ),
        default=DownloadStatus.CREATED,
        nullable=False,
    )
    error_type: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_seconds: Mapped[float | None] = mapped_column(Float)

    user = relationship("User", back_populates="downloads")
