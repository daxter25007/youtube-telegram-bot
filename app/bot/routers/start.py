from __future__ import annotations

from collections.abc import Collection

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.admin import admin_menu
from app.bot.keyboards.subscriptions import subscription_keyboard
from app.config import Settings
from app.database.repositories.channels import ChannelRepository
from app.services.subscriptions import SubscriptionService
from app.utils.auth import is_admin

router = Router(name="start")

WELCOME_TEXT = (
    "💙 <b>Привет! Я помогу скачать медиа с YouTube, TikTok, Instagram и Pinterest.</b>\n\n"
    "💻 <b>Как пользоваться:</b>\n"
    "1. Откройте нужное видео или публикацию.\n"
    "2. Нажмите «Поделиться» → «Копировать ссылку».\n"
    "3. Отправьте ссылку этому боту.\n"
    "4. Выберите доступное качество.\n"
    "5. Дождитесь готового файла.\n\n"
    "🔗 <b>Поддерживаются:</b> YouTube, Shorts, публичные публикации TikTok, "
    "Instagram Reels и посты, Pinterest Pins и Stories.\n\n"
    "Просто отправьте ссылку в чат 👇"
)


def start_menu(user_id: int, admin_ids: Collection[int]) -> InlineKeyboardMarkup | None:
    return admin_menu() if is_admin(user_id, admin_ids) else None


async def show_welcome(
    target: Message,
    *,
    bot: Bot,
    user_id: int,
    session: AsyncSession,
    settings: Settings,
    subscription_service: SubscriptionService,
) -> bool:
    await target.answer(
        WELCOME_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=start_menu(user_id, settings.admin_ids),
    )
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, user_id, channels)
    if missing:
        await target.answer(
            "📢 Для использования бота подпишитесь на каналы:",
            reply_markup=subscription_keyboard(missing),
        )
        return False
    return True


@router.message(CommandStart())
async def command_start(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    subscription_service: SubscriptionService,
) -> None:
    if message.from_user:
        await show_welcome(
            message,
            bot=bot,
            user_id=message.from_user.id,
            session=session,
            settings=settings,
            subscription_service=subscription_service,
        )


@router.callback_query(F.data == "sub:check")
async def check_subscription(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    subscription_service: SubscriptionService,
) -> None:
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, callback.from_user.id, channels)
    if missing:
        await callback.answer("Подписка найдена не на все каналы.", show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=subscription_keyboard(missing))
        return
    await callback.answer("Подписка подтверждена!")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("✅ Подписка подтверждена. Отправьте ссылку на медиа.")
