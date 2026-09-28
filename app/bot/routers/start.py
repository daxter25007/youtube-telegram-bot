from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.subscriptions import subscription_keyboard
from app.database.repositories.channels import ChannelRepository
from app.services.subscriptions import SubscriptionService

router = Router(name="start")


async def show_welcome(
    target: Message,
    *,
    bot: Bot,
    user_id: int,
    session: AsyncSession,
    subscription_service: SubscriptionService,
) -> bool:
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, user_id, channels)
    if missing:
        await target.answer(
            "📢 Для использования бота подпишитесь на каналы:",
            reply_markup=subscription_keyboard(missing),
        )
        return False
    await target.answer("👋 Отправьте ссылку на видео YouTube.")
    return True


@router.message(CommandStart())
async def command_start(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    subscription_service: SubscriptionService,
) -> None:
    if message.from_user:
        await show_welcome(
            message,
            bot=bot,
            user_id=message.from_user.id,
            session=session,
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
        await callback.message.edit_text("✅ Подписка подтверждена. Отправьте ссылку на YouTube.")
