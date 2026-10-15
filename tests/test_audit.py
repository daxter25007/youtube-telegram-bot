from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, Message, TelegramObject, Update, User

from app.bot.middlewares.audit import AuditMiddleware, _action_fields
from app.services.media.models import MediaSource
from app.utils.audit import audit_event


def test_audit_event_is_single_line_and_normalizes_values(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.audit")

    audit_event(
        "download_created",
        source=MediaSource.TIKTOK,
        title="Первая строка\nВторая строка",
        cache_hit=False,
    )

    assert caplog.messages == [
        'event="download_created" source="tiktok" '
        'title="Первая строка\\nВторая строка" cache_hit=false'
    ]


def test_command_action_does_not_log_arguments() -> None:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=20, type="private"),
        from_user=User(id=10, is_bot=False, first_name="Test"),
        text="/start private-argument",
    )
    update = Update(update_id=30, message=message)

    assert _action_fields(update) == {"action": "command", "command": "/start"}


@pytest.mark.asyncio
async def test_audit_middleware_logs_action_result(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.audit")
    user = User(id=10, is_bot=False, first_name="Test", username="tester")
    chat = Chat(id=20, type="private")
    update = Update(update_id=30)

    async def handler(event: TelegramObject, data: dict[str, object]) -> str:
        return "handled"

    result = await AuditMiddleware()(
        handler,
        update,
        {"event_update": update, "event_from_user": user, "event_chat": chat},
    )

    assert result == "handled"
    assert 'event="user_action_started"' in caplog.messages[0]
    assert "telegram_user_id=10" in caplog.messages[0]
    assert 'username="tester"' in caplog.messages[0]
    assert 'event="user_action_finished"' in caplog.messages[1]
    assert "duration_ms=" in caplog.messages[1]
