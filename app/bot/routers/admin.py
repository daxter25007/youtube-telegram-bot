from __future__ import annotations

import platform

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.admin import (
    admin_menu,
    channel_actions,
    channel_confirmation,
    channels_menu,
)
from app.bot.states.admin import AddChannelState
from app.config import Settings
from app.database.repositories.channels import ChannelRepository
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.users import UserRepository
from app.services.queue import DownloadQueue
from app.services.statistics import StatisticsService
from app.utils.auth import is_admin
from app.utils.filesize import human_size

router = Router(name="admin")


def _authorized(user_id: int, settings: Settings) -> bool:
    return is_admin(user_id, settings.admin_ids)


@router.message(Command("admin"))
async def admin_command(message: Message, settings: Settings) -> None:
    if not message.from_user or not _authorized(message.from_user.id, settings):
        await message.answer("⛔ Команда недоступна.")
        return
    await message.answer("Панель администратора", reply_markup=admin_menu())


@router.callback_query(F.data.startswith("admin:"))
async def admin_callbacks(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
    settings: Settings,
    statistics_service: StatisticsService,
    download_queue: DownloadQueue,
) -> None:
    if not callback.data or not _authorized(callback.from_user.id, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    action = callback.data
    if action == "admin:menu":
        await state.clear()
        await callback.message.edit_text("Панель администратора", reply_markup=admin_menu())
    elif action == "admin:stats":
        data = await statistics_service.summary(session)
        users = data["users"]
        downloads = data["downloads"]
        qualities = ", ".join(f"{row[0]}p — {row[1]}" for row in data["qualities"]) or "нет"
        top = (
            "\n".join(
                f"{index}. @{row[1] or row[0]} — {row[2]}"
                for index, row in enumerate(data["top_users"], 1)
            )
            or "нет"
        )
        await callback.message.answer(
            "📊 Статистика\n\n"
            f"Пользователи: {users[0]}\n"
            f"Сегодня / 7 / 30 дней: {users[1]} / {users[2]} / {users[3]}\n\n"
            f"Запросы: {downloads[0]}\nУспешно: {downloads[1]}\nОшибки: {downloads[2]}\n"
            f"Сегодня / 7 / 30 дней: {downloads[3]} / {downloads[4]} / {downloads[5]}\n"
            f"Отправлено: {human_size(int(downloads[6]))}\n"
            f"Средний размер: {human_size(int(downloads[7]))}\n"
            f"Среднее время: {float(downloads[8]):.1f} сек.\n\n"
            f"Качества: {qualities}\n\nTOP-10:\n{top}"
        )
    elif action == "admin:users":
        users = await UserRepository(session).recent()
        text = (
            "\n".join(
                f"{user.telegram_id} @{user.username or '—'} — {user.downloads_count}"
                for user in users
            )
            or "Пользователей нет."
        )
        await callback.message.answer(f"👥 Последние пользователи:\n{text}")
    elif action == "admin:downloads":
        downloads = await DownloadRepository(session).recent()
        text = (
            "\n".join(
                f"#{item.id} {item.requested_quality}p — {item.status.value}" for item in downloads
            )
            or "Загрузок нет."
        )
        await callback.message.answer(f"📥 Последние загрузки:\n{text}")
    elif action == "admin:channels":
        await callback.message.edit_text("📢 Обязательные каналы", reply_markup=channels_menu())
    elif action == "admin:channel:list":
        channels = await ChannelRepository(session).all()
        if not channels:
            await callback.message.answer("Каналы не добавлены.")
        for channel in channels:
            status = "✅ Активен" if channel.is_active else "⛔ Отключён"
            await callback.message.answer(
                f"{channel.title}\n{channel.username or 'без username'}\n"
                f"Chat ID: {channel.chat_id}\n{status}",
                reply_markup=channel_actions(channel),
            )
    elif action == "admin:channel:add":
        await state.set_state(AddChannelState.waiting_for_reference)
        await callback.message.answer("Отправьте @channel или -100xxxxxxxxxx")
    elif action == "admin:channel:confirm":
        data = await state.get_data()
        if not data.get("chat_id"):
            await callback.answer("Данные устарели.", show_alert=True)
            return
        await ChannelRepository(session).add(
            chat_id=int(data["chat_id"]),
            username=data.get("username"),
            title=str(data["title"]),
            invite_url=str(data["invite_url"]),
        )
        await state.clear()
        await callback.message.edit_text("✅ Канал добавлен.", reply_markup=channels_menu())
    elif action == "admin:channel:cancel":
        await state.clear()
        await callback.message.edit_text("Добавление отменено.", reply_markup=channels_menu())
    elif action.startswith("admin:channel:toggle:"):
        channel_id = int(action.rsplit(":", 1)[1])
        await ChannelRepository(session).toggle(channel_id)
        await callback.message.edit_text("Статус канала изменён.", reply_markup=channels_menu())
    elif action.startswith("admin:channel:delete:"):
        channel_id = int(action.rsplit(":", 1)[1])
        await ChannelRepository(session).delete(channel_id)
        await callback.message.edit_text("Канал удалён.", reply_markup=channels_menu())
    elif action == "admin:system":
        await callback.message.answer(
            "⚙️ Система\n"
            f"Python: {platform.python_version()}\n"
            f"Очередь: {download_queue.pending_count}\n"
            f"Лимит файла: {settings.max_upload_mb} MB\n"
            f"Параллельных загрузок: {settings.max_concurrent_downloads}"
        )
    await callback.answer()


@router.message(AddChannelState.waiting_for_reference, F.text)
async def receive_channel_reference(
    message: Message,
    bot: Bot,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not message.from_user or not _authorized(message.from_user.id, settings) or not message.text:
        return
    reference = message.text.strip()
    if not (reference.startswith("@") or reference.startswith("-100")):
        await message.answer("Используйте @channel или numeric ID вида -100xxxxxxxxxx.")
        return
    try:
        chat = await bot.get_chat(int(reference) if reference.startswith("-100") else reference)
    except Exception:
        await message.answer("Не удалось получить канал. Проверьте ID и права бота.")
        return
    if chat.type != ChatType.CHANNEL:
        await message.answer("Указанный чат не является каналом.")
        return
    try:
        bot_user = await bot.get_me()
        bot_member = await bot.get_chat_member(chat.id, bot_user.id)
    except Exception:
        await message.answer("Не удалось проверить права бота в канале.")
        return
    if bot_member.status not in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
        await message.answer("Сначала добавьте бота администратором канала.")
        return
    invite_url = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not invite_url:
        await message.answer(
            "У канала нет публичного username, а бот не видит invite link. "
            "Добавьте бота администратором канала."
        )
        return
    await state.update_data(
        chat_id=chat.id,
        username=f"@{chat.username}" if chat.username else None,
        title=chat.title or "Канал",
        invite_url=invite_url,
    )
    await state.set_state(AddChannelState.waiting_for_confirmation)
    await message.answer(
        f"Название: {chat.title}\nUsername: @{chat.username or '—'}\nChat ID: {chat.id}",
        reply_markup=channel_confirmation(),
    )
