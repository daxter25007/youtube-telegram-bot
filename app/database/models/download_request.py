from __future__ import annotations

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class DownloadRequest(TimestampMixin, Base):
    __tablename__ = "download_requests"
    __table_args__ = (Index("ix_download_requests_telegram_user_id", "telegram_user_id"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    youtube_url: Mapped[str] = mapped_column(Text, nullable=False)
    youtube_video_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
