from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.models import RequiredChannel


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users")],
            [InlineKeyboardButton(text="📥 Загрузки", callback_data="admin:downloads")],
            [InlineKeyboardButton(text="📢 Каналы", callback_data="admin:channels")],
            [InlineKeyboardButton(text="🩺 Мониторинг", callback_data="admin:monitoring")],
            [InlineKeyboardButton(text="⚙️ Система", callback_data="admin:system")],
        ]
    )


def channels_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить", callback_data="admin:channel:add")],
            [InlineKeyboardButton(text="📋 Список", callback_data="admin:channel:list")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:menu")],
        ]
    )


def channel_actions(channel: RequiredChannel) -> InlineKeyboardMarkup:
    toggle = "⏸ Отключить" if channel.is_active else "▶️ Включить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle, callback_data=f"admin:channel:toggle:{channel.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Удалить", callback_data=f"admin:channel:delete:{channel.id}"
                ),
            ]
        ]
    )


def channel_confirmation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Добавить", callback_data="admin:channel:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin:channel:cancel"),
            ]
        ]
    )
