from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CobaltResponseKind(StrEnum):
    TUNNEL = "tunnel"
    REDIRECT = "redirect"
    PICKER = "picker"


@dataclass(frozen=True, slots=True)
class CobaltMediaResponse:
    kind: CobaltResponseKind
    url: str
    filename: str
