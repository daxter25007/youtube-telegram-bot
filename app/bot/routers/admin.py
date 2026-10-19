from __future__ import annotations

import platform

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.admin import admin_menu
from app.config import Settings
from app.database.models import Download
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.users import UserRepository
from app.services.cobalt.client import CobaltClient
from app.services.media.backends.base import DownloadBackend
from app.services.media.models import MediaSource
from app.services.monitoring import MonitoringService
from app.services.queue import DownloadQueue
from app.services.statistics import StatisticsService
from app.utils.auth import is_admin
from app.utils.filesize import human_size

router = Router(name="admin")


def _authorized(user_id: int, settings: Settings) -> bool:
    return is_admin(user_id, settings.admin_ids)


def _download_summary_line(download: Download) -> str:
    source = {
        MediaSource.YOUTUBE: "YouTube",
        MediaSource.RUTUBE: "Rutube",
        MediaSource.TIKTOK: "TikTok",
        MediaSource.INSTAGRAM: "Instagram",
        MediaSource.PINTEREST: "Pinterest",
        MediaSource.VK: "VK",
    }[download.source]
    if download.requested_quality is not None:
        option = f"{download.requested_quality}p"
    elif download.requested_option == "best_video":
        option = "лучшее качество"
    else:
        option = download.requested_option.replace("_", " ")
    backend = getattr(download, "backend", None)
    backend_text = f" · {backend.value}" if isinstance(backend, DownloadBackend) else ""
    return f"#{download.id} {source} · {option}{backend_text} — {download.status.value}"


async def _cobalt_status_text(client: CobaltClient | None) -> str:
    if client is None:
        return "отключён"
    return "доступен" if await client.healthcheck() else "недоступен"


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
    settings: Settings,
    statistics_service: StatisticsService,
    download_queue: DownloadQueue,
    cobalt_client: CobaltClient | None,
    monitoring_service: MonitoringService,
) -> None:
    if not callback.data or not _authorized(callback.from_user.id, settings):
        await callback.answer("Недостаточно прав.", show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    action = callback.data
    if action == "admin:menu":
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
        text = "\n".join(_download_summary_line(item) for item in downloads) or "Загрузок нет."
        await callback.message.answer(f"📥 Последние загрузки:\n{text}")
    elif action == "admin:monitoring":
        report = await monitoring_service.report(
            bot=bot,
            session=session,
            cobalt_client=cobalt_client,
            download_queue=download_queue,
            temp_root=settings.temp_root,
        )
        await callback.message.answer(report)
    elif action == "admin:system":
        cobalt_status = await _cobalt_status_text(cobalt_client)
        await callback.message.answer(
            "⚙️ Система\n"
            f"Python: {platform.python_version()}\n"
            f"Очередь: {download_queue.pending_count}\n"
            f"Лимит файла: {settings.max_upload_mb} MB\n"
            f"Параллельных загрузок: {settings.max_concurrent_downloads}\n"
            f"Cobalt: {cobalt_status}"
        )
    await callback.answer()
