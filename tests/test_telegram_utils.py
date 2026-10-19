from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText

from app.utils.telegram import TELEGRAM_EDIT_MAX_ATTEMPTS, edit_text_with_retry


class FakeMessage:
    def __init__(self, errors: list[Exception]) -> None:
        self.chat = type("Chat", (), {"id": 123})()
        self.errors = errors
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def edit_text(self, text: str, **kwargs: object) -> bool:
        self.calls.append((text, kwargs))
        if self.errors:
            raise self.errors.pop(0)
        return True


def retry_after_error(seconds: int = 8) -> TelegramRetryAfter:
    return TelegramRetryAfter(
        method=EditMessageText(chat_id=123, message_id=456, text="result"),
        message="Too Many Requests",
        retry_after=seconds,
    )


@pytest.mark.asyncio
async def test_edit_text_retries_after_telegram_flood_control(monkeypatch) -> None:
    message = FakeMessage([retry_after_error()])
    sleep = AsyncMock()
    monkeypatch.setattr("app.utils.telegram.asyncio.sleep", sleep)

    result = await edit_text_with_retry(
        message,  # type: ignore[arg-type]
        "result",
        reply_markup="keyboard",
    )

    assert result is True
    assert message.calls == [
        ("result", {"reply_markup": "keyboard"}),
        ("result", {"reply_markup": "keyboard"}),
    ]
    sleep.assert_awaited_once_with(8)


@pytest.mark.asyncio
async def test_edit_text_stops_after_bounded_number_of_attempts(monkeypatch) -> None:
    message = FakeMessage([retry_after_error()] * TELEGRAM_EDIT_MAX_ATTEMPTS)
    sleep = AsyncMock()
    monkeypatch.setattr("app.utils.telegram.asyncio.sleep", sleep)

    with pytest.raises(TelegramRetryAfter):
        await edit_text_with_retry(message, "result")  # type: ignore[arg-type]

    assert len(message.calls) == TELEGRAM_EDIT_MAX_ATTEMPTS
    assert sleep.await_count == TELEGRAM_EDIT_MAX_ATTEMPTS - 1
