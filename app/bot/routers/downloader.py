from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.quality import quality_keyboard
from app.bot.keyboards.subscriptions import subscription_keyboard
from app.config import Settings
from app.database.models import DownloadRequest, DownloadStatus, User
from app.database.repositories.channels import ChannelRepository
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.media_cache import MediaCacheRepository
from app.database.repositories.requests import DownloadRequestRepository
from app.services.download_manager import DownloadManager
from app.services.media.errors import MetadataError, MetadataErrorKind
from app.services.media.metadata import YtDlpMetadataService
from app.services.media.models import DownloadOption, MediaKind, MediaSource
from app.services.media.registry import MediaRegistry
from app.services.queue import DownloadQueue, QueueClosedError, UserDownloadLimitError
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.subscriptions import SubscriptionService
from app.utils.audit import audit_event
from app.utils.auth import is_admin
from app.utils.filesize import format_duration

logger = logging.getLogger(__name__)
router = Router(name="downloader")
PUBLICATION_SOURCES = frozenset({MediaSource.TIKTOK, MediaSource.INSTAGRAM, MediaSource.PINTEREST})


def _option_from_json(metadata: dict[str, Any], option_key: str) -> DownloadOption | None:
    for item in metadata.get("options", []):
        if not isinstance(item, dict) or item.get("key") != option_key:
            continue
        try:
            return DownloadOption(
                key=str(item["key"]),
                label=str(item["label"]),
                media_kind=MediaKind(str(item["media_kind"])),
                height=int(item["height"]) if item.get("height") is not None else None,
                estimated_size=(
                    int(item["estimated_size"]) if item.get("estimated_size") is not None else None
                ),
                fps=float(item["fps"]) if item.get("fps") is not None else None,
                codec=str(item["codec"]) if item.get("codec") else None,
                container=str(item["container"]) if item.get("container") else None,
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


async def _set_download_status(
    session: AsyncSession,
    download_id: int,
    status: DownloadStatus,
    *,
    error_type: str,
) -> None:
    await DownloadRepository(session).set_status(
        download_id,
        status,
        error_type=error_type,
    )
    await session.commit()


async def _enqueue_option(
    *,
    progress_message: Message,
    telegram_user_id: int,
    request: DownloadRequest,
    option: DownloadOption,
    session: AsyncSession,
    settings: Settings,
    media_registry: MediaRegistry,
    download_queue: DownloadQueue,
    download_manager: DownloadManager,
    bot: Bot,
) -> None:
    metadata = request.metadata_json
    title = str(metadata.get("title") or "Видео")[:512]
    adapter = media_registry.adapter_for(request.source)
    try:
        policy = adapter.download_policy(option.key)
    except ValueError:
        audit_event(
            "download_option_rejected",
            telegram_user_id=telegram_user_id,
            request_id=request.id,
            source=request.source,
            source_media_id=request.source_media_id,
            option=option.key,
            reason="stale_option",
        )
        await progress_message.edit_text("Ссылка или выбранный вариант устарели.")
        return
    download = await DownloadRepository(session).create(
        user_id=request.user_id,
        source=request.source,
        media_kind=option.media_kind,
        source_media_id=request.source_media_id,
        source_url=request.source_url,
        video_title=title,
        requested_option=option.key,
        requested_quality=(option.height if request.source is MediaSource.YOUTUBE else None),
        status=DownloadStatus.CREATED,
    )
    await DownloadRequestRepository(session).delete(request.id)
    await session.commit()
    audit_event(
        "download_created",
        telegram_user_id=telegram_user_id,
        chat_id=progress_message.chat.id,
        download_id=download.id,
        source=request.source,
        source_media_id=request.source_media_id,
        media_kind=option.media_kind,
        option=option.key,
        title=title,
    )
    try:
        if request.source in PUBLICATION_SOURCES:
            delivered_from_cache = await download_manager.try_cached_publication(
                bot=bot,
                chat_id=progress_message.chat.id,
                user_id=request.user_id,
                download_id=download.id,
                source=request.source,
                source_media_id=request.source_media_id,
                title=title,
                source_url=request.source_url,
                progress_message=progress_message,
            )
        else:
            delivered_from_cache = await download_manager.try_cached(
                bot=bot,
                chat_id=progress_message.chat.id,
                user_id=request.user_id,
                download_id=download.id,
                source=request.source,
                source_media_id=request.source_media_id,
                media_kind=option.media_kind,
                option_key=option.key,
                title=title,
                source_url=request.source_url,
                progress_message=progress_message,
            )
    except TelegramAPIError:
        logger.exception("Telegram cache delivery failed for download_id=%s", download.id)
        await _set_download_status(
            session,
            download.id,
            DownloadStatus.FAILED,
            error_type="telegram_api",
        )
        await progress_message.edit_text("❌ Не удалось отправить файл в Telegram.")
        audit_event(
            "download_finished",
            level=logging.ERROR,
            telegram_user_id=telegram_user_id,
            download_id=download.id,
            status=DownloadStatus.FAILED,
            outcome="cache_delivery_failed",
        )
        return
    if delivered_from_cache:
        return
    await DownloadRepository(session).set_status(download.id, DownloadStatus.QUEUED)
    await session.commit()
    await progress_message.edit_text("⏳ Загрузка поставлена в очередь")

    async def run_job() -> None:
        await download_manager.run(
            bot=bot,
            chat_id=progress_message.chat.id,
            telegram_user_id=telegram_user_id,
            user_id=request.user_id,
            download_id=download.id,
            source_url=request.source_url,
            source=request.source,
            source_media_id=request.source_media_id,
            media_kind=option.media_kind,
            option_key=option.key,
            policy=policy,
            title=title,
            progress_message=progress_message,
        )

    async def cancel_job() -> None:
        await download_manager.cancel_queued(download.id, progress_message)

    try:
        position = await download_queue.submit(
            telegram_user_id,
            run_job,
            on_cancel=cancel_job,
        )
    except UserDownloadLimitError:
        await _set_download_status(
            session,
            download.id,
            DownloadStatus.CANCELLED,
            error_type="per_user_limit",
        )
        await progress_message.edit_text("⏳ Дождитесь завершения текущей загрузки.")
        audit_event(
            "download_queue_rejected",
            telegram_user_id=telegram_user_id,
            download_id=download.id,
            reason="per_user_limit",
        )
        return
    except QueueClosedError:
        await _set_download_status(
            session,
            download.id,
            DownloadStatus.CANCELLED,
            error_type="shutdown",
        )
        await progress_message.edit_text("Бот временно завершает работу.")
        audit_event(
            "download_queue_rejected",
            telegram_user_id=telegram_user_id,
            download_id=download.id,
            reason="shutdown",
        )
        return
    audit_event(
        "download_queued",
        telegram_user_id=telegram_user_id,
        download_id=download.id,
        position=position,
    )
    if position > settings.max_concurrent_downloads:
        await progress_message.edit_text(
            "⏳ Сейчас выполняется несколько загрузок. Ваша загрузка поставлена в очередь."
        )


def _metadata_error_text(error: MetadataError) -> str:
    if error.kind is MetadataErrorKind.RESTRICTED:
        return "❌ Это видео невозможно скачать с текущего сервера."
    if error.kind is MetadataErrorKind.AUTHENTICATION:
        return "❌ Для этого видео требуется авторизация. Попробуйте другую ссылку."
    if error.kind is MetadataErrorKind.UNSUPPORTED_MEDIA:
        return "❌ Фотопубликации пока не поддерживаются. Отправьте ссылку на видео."
    if error.kind is MetadataErrorKind.TOO_LARGE:
        return "⚠️ Видео больше допустимого размера для отправки в Telegram."
    if error.kind is MetadataErrorKind.EXTRACTOR_CHANGED:
        return "❌ TikTok временно не отдаёт видео серверу. Попробуйте позже."
    return "❌ Не удалось получить видео. Возможно, оно удалено, приватное или недоступно."


@router.message(F.text)
async def receive_url(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    db_user: User,
    settings: Settings,
    media_registry: MediaRegistry,
    metadata_service: YtDlpMetadataService,
    rate_limiter: SlidingWindowRateLimiter,
    subscription_service: SubscriptionService,
    download_queue: DownloadQueue,
    download_manager: DownloadManager,
) -> None:
    if not message.text or message.text.startswith("/") or not message.from_user:
        return
    chat_id = getattr(getattr(message, "chat", None), "id", message.from_user.id)
    parsed = media_registry.parse(message.text)
    if parsed is None:
        audit_event(
            "media_link_rejected",
            telegram_user_id=message.from_user.id,
            username=getattr(message.from_user, "username", None),
            chat_id=chat_id,
            reason="invalid_url",
        )
        await message.answer("❌ Отправьте корректную ссылку YouTube, TikTok или Instagram.")
        return
    audit_event(
        "media_link_received",
        telegram_user_id=message.from_user.id,
        username=getattr(message.from_user, "username", None),
        chat_id=chat_id,
        source=parsed.source,
        source_media_id=parsed.media_id,
    )
    if not is_admin(message.from_user.id, settings.admin_ids) and not await rate_limiter.allow(
        message.from_user.id
    ):
        audit_event(
            "media_link_rejected",
            telegram_user_id=message.from_user.id,
            source=parsed.source,
            source_media_id=parsed.media_id,
            reason="rate_limit",
        )
        await message.answer("⏳ Слишком много ссылок. Попробуйте немного позже.")
        return
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, message.from_user.id, channels)
    if missing:
        audit_event(
            "media_link_rejected",
            telegram_user_id=message.from_user.id,
            source=parsed.source,
            source_media_id=parsed.media_id,
            reason="subscription",
        )
        await message.answer(
            "Для использования бота подпишитесь на каналы:",
            reply_markup=subscription_keyboard(missing),
        )
        return

    await session.commit()
    status_message = await message.answer("🔎 Получаю информацию о видео...")
    adapter = media_registry.adapter_for(parsed.source)
    metadata = adapter.initial_metadata(parsed)
    if metadata is None:
        try:
            metadata = await metadata_service.fetch(parsed)
        except TimeoutError:
            audit_event(
                "metadata_failed",
                telegram_user_id=message.from_user.id,
                source=parsed.source,
                source_media_id=parsed.media_id,
                reason="timeout",
            )
            await status_message.edit_text("⌛ Не удалось получить информацию о видео вовремя.")
            return
        except MetadataError as error:
            logger.info(
                "Metadata extraction rejected source=%s media_id=%s kind=%s",
                parsed.source.value,
                parsed.media_id or "unknown",
                error.kind.value,
            )
            audit_event(
                "metadata_failed",
                telegram_user_id=message.from_user.id,
                source=parsed.source,
                source_media_id=parsed.media_id,
                reason=error.kind,
            )
            await status_message.edit_text(_metadata_error_text(error))
            return

    request = await DownloadRequestRepository(session).create(
        user_id=db_user.id,
        telegram_user_id=message.from_user.id,
        source=metadata.source,
        media_kind=metadata.media_kind,
        source_url=metadata.source_url,
        source_media_id=metadata.media_id,
        metadata=metadata.as_dict(),
    )
    await session.commit()
    audit_event(
        "metadata_ready",
        telegram_user_id=message.from_user.id,
        request_id=request.id,
        source=metadata.source,
        source_media_id=metadata.media_id,
        media_kind=metadata.media_kind,
        title=metadata.title,
        duration_seconds=metadata.duration,
        option_count=len(metadata.options),
    )
    if adapter.automatic_option:
        option = next(
            (item for item in metadata.options if item.key == adapter.automatic_option),
            None,
        )
        if option is None:
            await status_message.edit_text("❌ Не найден подходящий формат видео.")
            return
        await status_message.edit_text(
            f"🎬 {metadata.title}\n\n⏱ {format_duration(metadata.duration)}"
        )
        await _enqueue_option(
            progress_message=status_message,
            telegram_user_id=message.from_user.id,
            request=request,
            option=option,
            session=session,
            settings=settings,
            media_registry=media_registry,
            download_queue=download_queue,
            download_manager=download_manager,
            bot=bot,
        )
        return
    cached_option_keys: set[str] = set()
    for media_kind in {option.media_kind for option in metadata.options}:
        cached_option_keys.update(
            await MediaCacheRepository(session).cached_option_keys(
                source=metadata.source,
                source_media_id=metadata.media_id,
                media_kind=media_kind,
            )
        )
    await status_message.edit_text(
        f"🎬 {metadata.title}\n\n⏱ {format_duration(metadata.duration)}\n\nВыберите качество:",
        reply_markup=quality_keyboard(
            request.id,
            list(metadata.options),
            cached_option_keys=cached_option_keys,
        ),
    )


@router.callback_query(F.data.startswith("media:"))
async def select_media_option(
    callback: CallbackQuery,
    bot: Bot,
    session: AsyncSession,
    settings: Settings,
    media_registry: MediaRegistry,
    download_queue: DownloadQueue,
    download_manager: DownloadManager,
    subscription_service: SubscriptionService,
) -> None:
    if not isinstance(callback.message, Message) or not callback.data:
        await callback.answer("Недоступное сообщение.", show_alert=True)
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректный запрос.", show_alert=True)
        return
    request_id, option_key = parts[1], parts[2]
    request = await DownloadRequestRepository(session).get_owned_valid(
        request_id,
        callback.from_user.id,
        settings.download_request_ttl_minutes,
    )
    if request is None:
        audit_event(
            "download_option_rejected",
            telegram_user_id=callback.from_user.id,
            request_id=request_id,
            option=option_key,
            reason="expired_or_not_owned",
        )
        await callback.answer("Ссылка устарела. Отправьте видео ещё раз.", show_alert=True)
        return
    option = _option_from_json(request.metadata_json, option_key)
    if option is None:
        audit_event(
            "download_option_rejected",
            telegram_user_id=callback.from_user.id,
            request_id=request.id,
            source=request.source,
            source_media_id=request.source_media_id,
            option=option_key,
            reason="unavailable",
        )
        await callback.answer("Этот вариант недоступен.", show_alert=True)
        return
    channels = await ChannelRepository(session).active()
    missing = await subscription_service.missing_channels(bot, callback.from_user.id, channels)
    if missing:
        audit_event(
            "download_option_rejected",
            telegram_user_id=callback.from_user.id,
            request_id=request.id,
            source=request.source,
            source_media_id=request.source_media_id,
            option=option.key,
            reason="subscription",
        )
        await callback.answer("Сначала подпишитесь на обязательные каналы.", show_alert=True)
        await callback.message.answer(
            "Для использования бота подпишитесь на каналы:",
            reply_markup=subscription_keyboard(missing),
        )
        return
    await _enqueue_option(
        progress_message=callback.message,
        telegram_user_id=callback.from_user.id,
        request=request,
        option=option,
        session=session,
        settings=settings,
        media_registry=media_registry,
        download_queue=download_queue,
        download_manager=download_manager,
        bot=bot,
    )
    await callback.answer()
