from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class MediaSource(StrEnum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


class MediaKind(StrEnum):
    VIDEO = "video"
    PHOTO = "photo"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class ParsedMediaUrl:
    source: MediaSource
    normalized_url: str
    media_id: str | None


@dataclass(frozen=True, slots=True)
class DownloadOption:
    key: str
    label: str
    media_kind: MediaKind
    height: int | None = None
    estimated_size: int | None = None
    fps: float | None = None
    codec: str | None = None
    container: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "media_kind": self.media_kind.value,
            "height": self.height,
            "estimated_size": self.estimated_size,
            "fps": self.fps,
            "codec": self.codec,
            "container": self.container,
        }


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    source: MediaSource
    media_id: str
    source_url: str
    title: str
    duration: int | None
    thumbnail: str | None
    media_kind: MediaKind
    options: tuple[DownloadOption, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "media_id": self.media_id,
            "source_url": self.source_url,
            "title": self.title,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "media_kind": self.media_kind.value,
            "options": [option.as_dict() for option in self.options],
        }


@dataclass(frozen=True, slots=True)
class MediaPart:
    key: str
    media_kind: MediaKind
    position: int
    filename: str | None = None
    mime_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "media_kind": self.media_kind.value,
            "position": self.position,
            "filename": self.filename,
            "mime_type": self.mime_type,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> MediaPart:
        return cls(
            key=str(value["key"]),
            media_kind=MediaKind(str(value["media_kind"])),
            position=int(value["position"]),
            filename=str(value["filename"]) if value.get("filename") else None,
            mime_type=str(value["mime_type"]) if value.get("mime_type") else None,
        )


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    format_selector: str
    media_kind: MediaKind = MediaKind.VIDEO
    extract_audio: bool = False
    audio_format: str | None = None
    merge_output_format: str = "mp4"
    remux_video: str = "mp4"


@dataclass(frozen=True, slots=True)
class MediaArtifact:
    path: Path
    media_kind: MediaKind
    part_key: str = "media"
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    mime_type: str | None = None


@dataclass(frozen=True, slots=True)
class MediaPackage:
    artifacts: tuple[MediaArtifact, ...]
    failures: tuple[object, ...] = ()
