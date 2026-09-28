from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.quality import quality_keyboard
from app.bot.keyboards.subscriptions import subscription_keyboard
from app.config import Settings
from app.database.models import DownloadStatus, User
from app.database.repositories.channels import ChannelRepository
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.requests import DownloadRequestRepository
from app.services.download_manager import DownloadManager
from app.services.queue import DownloadQueue, QueueClosedError, UserDownloadLimitError
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.subscriptions import SubscriptionService
from app.services.youtube.formats import SUPPORTED_QUALITIES
from app.services.youtube.metadata import MetadataError, MetadataService
from app.utils.auth import is_admin
from app.utils.filesize import format_duration
from app.utils.urls import validate_youtube_url

logger = logging.getLogger(__name__)
router = Router(name="downloader")


@router.message(F.text)
async def receive_url(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    metadata_service: MetadataService,
    rate_limiter: SlidingWindowRateLimiter,
    subscription_service: SubscriptionService,
) -> None:
    if not message.text or message.text.startswith("/") or not message.from_user:
        return
    parsed = validate_youtube_url(message.text)
    if parsed is None:
        await message.answer("❌ Отправьте корректную ссылку YouTube (watch, youtu.be или shorts).")
        return
    if not is_admin(message.from_user.id, settings.admin_ids) and not await rate_limiter.allow(
        message.from_user.id
    ):
        await message.answer("⏳ Слишком много ссылок. Попробуйте немного позже.")
        return
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, message.from_user.id, channels)
    if missing:
        await message.answer(
            "Для использования бота подпишитесь на каналы:",
            reply_markup=subscription_keyboard(missing),
        )
        return

    await session.commit()
    status_message = await message.answer("🔎 Получаю информацию о видео...")
    try:
        metadata = await metadata_service.fetch(parsed.normalized_url)
    except TimeoutError:
        await status_message.edit_text("⌛ Не удалось получить информацию о видео вовремя.")
        return
    except MetadataError as exc:
        logger.info("Metadata extraction rejected video_id=%s", parsed.video_id)
        if exc.kind == "restricted":
            await status_message.edit_text("❌ Это видео невозможно скачать с текущего сервера.")
            return
        await status_message.edit_text(
            "❌ Не удалось получить видео. Возможно, оно удалено, приватное или недоступно."
        )
        return
    request = await DownloadRequestRepository(session).create(
        user_id=db_user.id,
        telegram_user_id=message.from_user.id,
        youtube_url=parsed.normalized_url,
        youtube_video_id=metadata.video_id,
        metadata=metadata.as_dict(),
    )
    await status_message.edit_text(
        f"🎬 {metadata.title}\n\n⏱ {format_duration(metadata.duration)}\n\nВыберите качество:",
        reply_markup=quality_keyboard(request.id, list(metadata.qualities)),
    )


@router.callback_query(F.data.startswith("quality:"))
async def select_quality(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    db_user: User,
    download_queue: DownloadQueue,
    download_manager: DownloadManager,
    subscription_service: SubscriptionService,
) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        await callback.answer("Недоступное сообщение.", show_alert=True)
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректный запрос.", show_alert=True)
        return
    request_id, quality_text = parts[1], parts[2]
    try:
        quality = int(quality_text)
    except ValueError:
        await callback.answer("Некорректное качество.", show_alert=True)
        return
    request_repo = DownloadRequestRepository(session)
    request = await request_repo.get_owned_valid(
        request_id,
        callback.from_user.id,
        settings.download_request_ttl_minutes,
    )
    if request is None:
        await callback.answer("Ссылка устарела. Отправьте видео ещё раз.", show_alert=True)
        return
    metadata = request.metadata_json
    available = {
        int(item["height"])
        for item in metadata.get("qualities", [])  # type: ignore[union-attr]
        if isinstance(item, dict) and isinstance(item.get("height"), int)
    }
    if quality not in SUPPORTED_QUALITIES or quality not in available:
        await callback.answer("Это качество недоступно.", show_alert=True)
        return
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, callback.from_user.id, channels)
    if missing:
        await callback.answer("Сначала подпишитесь на обязательные каналы.", show_alert=True)
        await callback.message.answer(
            "Для использования бота подпишитесь на каналы:",
            reply_markup=subscription_keyboard(missing),
        )
        return

    title = str(metadata.get("title") or "YouTube video")[:512]
    download = await DownloadRepository(session).create(
        user_id=db_user.id,
        youtube_video_id=request.youtube_video_id,
        youtube_url=request.youtube_url,
        video_title=title,
        requested_quality=quality,
        status=DownloadStatus.QUEUED,
    )
    await request_repo.delete(request.id)
    await session.commit()
    progress_message = await callback.message.edit_text("⏳ Загрузка поставлена в очередь")

    async def run_job() -> None:
        await download_manager.run(
            bot=bot,
            chat_id=callback.message.chat.id,
            telegram_user_id=callback.from_user.id,
            user_id=db_user.id,
            download_id=download.id,
            youtube_url=request.youtube_url,
            title=title,
            quality=quality,
            progress_message=progress_message,
        )

    async def cancel_job() -> None:
        await download_manager.cancel_queued(download.id, progress_message)

    try:
        position = await download_queue.submit(
            callback.from_user.id,
            run_job,
            on_cancel=cancel_job,
        )
    except UserDownloadLimitError:
        await DownloadRepository(session).set_status(
            download.id, DownloadStatus.CANCELLED, error_type="per_user_limit"
        )
        await callback.answer("У вас уже выполняется загрузка.", show_alert=True)
        await progress_message.edit_text("⏳ Дождитесь завершения текущей загрузки.")
        return
    except QueueClosedError:
        await DownloadRepository(session).set_status(
            download.id, DownloadStatus.CANCELLED, error_type="shutdown"
        )
        await callback.answer("Бот временно завершает работу.", show_alert=True)
        return
    if position > settings.max_concurrent_downloads:
        await progress_message.edit_text(
            "⏳ Сейчас выполняется несколько загрузок. Ваша загрузка поставлена в очередь."
        )
    await callback.answer()
