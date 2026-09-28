from __future__ import annotations


def is_too_large(size_bytes: int, max_bytes: int) -> bool:
    return size_bytes > max_bytes


def human_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "неизвестно"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "неизвестно"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
