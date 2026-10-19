from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message

logger = logging.getLogger(__name__)
TELEGRAM_EDIT_MAX_ATTEMPTS = 3


async def edit_text_with_retry(message: Message, text: str, **kwargs: Any) -> Message | bool:
    """Редактирует сообщение, соблюдая указанный Telegram интервал flood control."""
    for attempt in range(1, TELEGRAM_EDIT_MAX_ATTEMPTS + 1):
        try:
            return await message.edit_text(text, **kwargs)
        except TelegramRetryAfter as error:
            if attempt == TELEGRAM_EDIT_MAX_ATTEMPTS:
                raise
            logger.warning(
                "Telegram limited message edit chat_id=%s retry_after=%s attempt=%s",
                message.chat.id,
                error.retry_after,
                attempt,
            )
            await asyncio.sleep(error.retry_after)
    raise RuntimeError("unreachable")
