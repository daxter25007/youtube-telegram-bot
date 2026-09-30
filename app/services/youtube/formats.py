from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPORTED_QUALITIES = (144, 240, 360, 480, 720, 1080, 1440, 2160)


@dataclass(frozen=True, slots=True)
class QualityOption:
    height: int
    estimated_size: int | None
    fps: float | None
    codec: str | None
    container: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "height": self.height,
            "estimated_size": self.estimated_size,
            "fps": self.fps,
            "codec": self.codec,
            "container": self.container,
        }


def _size(format_data: dict[str, Any], duration: int | None) -> int | None:
    value = format_data.get("filesize") or format_data.get("filesize_approx")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    bitrate = format_data.get("tbr")
    if isinstance(bitrate, (int, float)) and bitrate > 0 and duration:
        return int(bitrate * 1000 / 8 * duration)
    return None


def _video_score(item: dict[str, Any]) -> tuple[int, int, float]:
    ext = str(item.get("ext") or "")
    codec = str(item.get("vcodec") or "")
    preference = int(ext == "mp4") * 4 + int(codec.startswith(("avc", "h264"))) * 4
    return preference, int(item.get("fps") or 0), float(item.get("tbr") or 0)


def _audio_score(item: dict[str, Any]) -> tuple[int, float]:
    ext = str(item.get("ext") or "")
    codec = str(item.get("acodec") or "")
    preference = int(ext in {"m4a", "mp4"}) * 4 + int(codec.startswith(("mp4a", "aac"))) * 4
    return preference, float(item.get("abr") or item.get("tbr") or 0)


def select_quality_options(
    formats: list[dict[str, Any]], duration: int | None, max_upload_bytes: int
) -> list[QualityOption]:
    video_formats = [
        item
        for item in formats
        if item.get("vcodec") not in (None, "none") and isinstance(item.get("height"), (int, float))
    ]
    audio_formats = [
        item
        for item in formats
        if item.get("acodec") not in (None, "none") and item.get("vcodec") == "none"
    ]
    best_audio = max(audio_formats, key=_audio_score, default=None)
    audio_size = _size(best_audio, duration) if best_audio else 0

    options: list[QualityOption] = []
    for height in SUPPORTED_QUALITIES:
        exact = [item for item in video_formats if int(item.get("height") or 0) == height]
        if not exact:
            continue
        chosen = max(exact, key=_video_score)
        video_size = _size(chosen, duration)
        estimated = None
        if video_size is not None and audio_size is not None:
            estimated = video_size + audio_size
        if estimated is not None and estimated > max_upload_bytes:
            continue
        options.append(
            QualityOption(
                height=height,
                estimated_size=estimated,
                fps=float(chosen["fps"]) if chosen.get("fps") else None,
                codec=str(chosen.get("vcodec") or "") or None,
                container=str(chosen.get("ext") or "") or None,
            )
        )
    return options


def build_format_selector(height: int) -> str:
    if height not in SUPPORTED_QUALITIES:
        raise ValueError("Unsupported quality")
    return (
        f"bestvideo[height<={height}][ext=mp4][vcodec^=avc]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"best[height<={height}]"
    )
