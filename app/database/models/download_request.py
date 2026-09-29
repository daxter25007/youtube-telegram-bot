from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.services.media.models import MediaKind, MediaSource


class DownloadRequest(TimestampMixin, Base):
    __tablename__ = "download_requests"
    __table_args__ = (Index("ix_download_requests_telegram_user_id", "telegram_user_id"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
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
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_media_id: Mapped[str] = mapped_column(String(128), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
