from __future__ import annotations

from collections.abc import Collection

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, Message

from app.bot.keyboards.admin import admin_menu
from app.config import Settings
from app.utils.auth import is_admin

router = Router(name="start")

WELCOME_TEXT = (
    "💙 <b>Привет! Я помогу скачать медиа с YouTube, Rutube, TikTok, Instagram, Pinterest "
    "и VK.</b>\n\n"
    "💻 <b>Как пользоваться:</b>\n"
    "1. Откройте нужное видео или публикацию.\n"
    "2. Нажмите «Поделиться» → «Копировать ссылку».\n"
    "3. Отправьте ссылку этому боту.\n"
    "4. Выберите доступное качество.\n"
    "5. Дождитесь готового файла.\n\n"
    "🔗 <b>Поддерживаются:</b> YouTube, Rutube и их Shorts, публичные публикации TikTok, "
    "Instagram Reels и посты, Pinterest Pins и Stories, видео VK и VK Клипы.\n\n"
    "Просто отправьте ссылку в чат 👇"
)


def start_menu(user_id: int, admin_ids: Collection[int]) -> InlineKeyboardMarkup | None:
    return admin_menu() if is_admin(user_id, admin_ids) else None


async def show_welcome(
    target: Message,
    *,
    user_id: int,
    settings: Settings,
) -> None:
    await target.answer(
        WELCOME_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=start_menu(user_id, settings.admin_ids),
    )


@router.message(CommandStart())
async def command_start(
    message: Message,
    settings: Settings,
) -> None:
    if message.from_user:
        await show_welcome(
            message,
            user_id=message.from_user.id,
            settings=settings,
        )
