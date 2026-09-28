from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.youtube.formats import QualityOption


def quality_keyboard(request_id: str, qualities: list[QualityOption]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{quality.height}p",
                callback_data=f"quality:{request_id}:{quality.height}",
            )
        ]
        for quality in qualities
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
