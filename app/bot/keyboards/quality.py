from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.media.models import DownloadOption


def quality_keyboard(
    request_id: str,
    options: list[DownloadOption],
    *,
    cached_option_keys: set[str] | frozenset[str] = frozenset(),
) -> InlineKeyboardMarkup:
    video_buttons = [
        InlineKeyboardButton(
            text=f"{'⚡' if option.key in cached_option_keys else '⏳'} {option.label}",
            callback_data=f"media:{request_id}:{option.key}",
        )
        for option in options
        if option.media_kind.value == "video"
    ]
    rows = [video_buttons[index : index + 2] for index in range(0, len(video_buttons), 2)]
    rows.extend(
        [
            InlineKeyboardButton(
                text=f"{'⚡' if option.key in cached_option_keys else '⏳'} {option.label}",
                callback_data=f"media:{request_id}:{option.key}",
            )
        ]
        for option in options
        if option.media_kind.value != "video"
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
