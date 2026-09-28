from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import RequiredChannel


def subscription_keyboard(channels: list[RequiredChannel]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📢 {channel.title}", url=channel.invite_url)]
        for channel in channels
    ]
    rows.append([InlineKeyboardButton(text="✅ Проверить подписку", callback_data="sub:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
