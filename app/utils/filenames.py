from __future__ import annotations

import re

CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
UNSAFE_RE = re.compile(r"[\\/:*?\"<>|]+")
SPACE_RE = re.compile(r"\s+")


def sanitize_display_filename(title: str, suffix: str = ".mp4", max_length: int = 120) -> str:
    clean = CONTROL_RE.sub("", title)
    clean = UNSAFE_RE.sub("_", clean).replace("..", "_")
    clean = SPACE_RE.sub(" ", clean).strip(" ._")
    if not clean:
        clean = "video"
    available = max(1, max_length - len(suffix))
    clean = clean[:available].rstrip(" ._") or "video"
    return f"{clean}{suffix}"
