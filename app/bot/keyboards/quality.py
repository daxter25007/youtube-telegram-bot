from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.media.models import DownloadOption


def quality_keyboard(request_id: str, options: list[DownloadOption]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=option.label,
                callback_data=f"media:{request_id}:{option.key}",
            )
        ]
        for option in options
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
