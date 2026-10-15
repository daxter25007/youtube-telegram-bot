from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from app.utils.audit import audit_event, user_audit_fields


def _action_fields(event: TelegramObject) -> dict[str, object]:
    update = event if isinstance(event, Update) else None
    message = update.message if update else event if isinstance(event, Message) else None
    callback = (
        update.callback_query if update else event if isinstance(event, CallbackQuery) else None
    )
    if callback is not None:
        return {"action": "callback", "callback": callback.data}
    if message is not None:
        text = message.text or ""
        if text.startswith("/"):
            return {"action": "command", "command": text.split(maxsplit=1)[0].split("@", 1)[0]}
        if text:
            return {"action": "text_message"}
        return {"action": "message", "content_type": message.content_type}
    return {"action": type(event).__name__}


class AuditMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        started = time.monotonic()
        fields = {**user_audit_fields(data), **_action_fields(event)}
        audit_event("user_action_started", **fields)
        try:
            result = await handler(event, data)
        except Exception as error:
            audit_event(
                "user_action_failed",
                level=logging.ERROR,
                **fields,
                error_type=type(error).__name__,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise
        audit_event(
            "user_action_finished",
            **fields,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return result
