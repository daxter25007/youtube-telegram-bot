from __future__ import annotations

from typing import Any


def format_size(format_data: dict[str, Any], duration: int | None) -> int | None:
    value = format_data.get("filesize") or format_data.get("filesize_approx")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    bitrate = format_data.get("tbr")
    if isinstance(bitrate, (int, float)) and bitrate > 0 and duration:
        return int(bitrate * 1000 / 8 * duration)
    return None


def best_video_format(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [item for item in formats if item.get("vcodec") not in (None, "none")]
    return max(
        candidates,
        key=lambda item: (
            int(item.get("height") or 0),
            float(item.get("tbr") or 0),
            int(str(item.get("ext") or "") == "mp4"),
        ),
        default=None,
    )
