from __future__ import annotations

from sqlalchemy import JSON, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin
from app.services.media.models import MediaPart, MediaSource


class MediaManifest(TimestampMixin, Base):
    __tablename__ = "media_manifests"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_media_id",
            name="uq_media_manifests_source",
        ),
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
    parts_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)

    @property
    def parts(self) -> tuple[MediaPart, ...]:
        return tuple(MediaPart.from_dict(item) for item in self.parts_json)
