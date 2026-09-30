from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.services.media.models import MediaKind


class CobaltResponseKind(StrEnum):
    TUNNEL = "tunnel"
    REDIRECT = "redirect"
    PICKER = "picker"


class CobaltMode(StrEnum):
    AUTO = "auto"
    AUDIO = "audio"


@dataclass(frozen=True, slots=True)
class CobaltPreparedItem:
    part_key: str
    media_kind: MediaKind
    position: int
    url: str
    filename: str
    response_kind: CobaltResponseKind
    internal_origin: str | None = None


@dataclass(frozen=True, slots=True)
class CobaltMediaResponse:
    items: tuple[CobaltPreparedItem, ...]
