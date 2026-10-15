from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

audit_logger = logging.getLogger("app.audit")
MAX_AUDIT_VALUE_LENGTH = 512


def _normalize_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _render_value(value: object) -> str:
    normalized = _normalize_value(value)
    if isinstance(normalized, str) and len(normalized) > MAX_AUDIT_VALUE_LENGTH:
        normalized = f"{normalized[:MAX_AUDIT_VALUE_LENGTH]}…"
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def audit_event(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    """Записывает однострочное структурированное событие аудита."""
    pairs = [f"event={_render_value(event)}"]
    pairs.extend(f"{key}={_render_value(value)}" for key, value in fields.items())
    audit_logger.log(level, " ".join(pairs))


def user_audit_fields(data: dict[str, Any]) -> dict[str, object]:
    user = data.get("event_from_user")
    chat = data.get("event_chat")
    update = data.get("event_update")
    return {
        "update_id": getattr(update, "update_id", None),
        "telegram_user_id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "chat_id": getattr(chat, "id", None),
    }
