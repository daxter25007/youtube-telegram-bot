from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.database.repositories.users import UserRepository
from app.database.session import Database


class DatabaseUserMiddleware(BaseMiddleware):
    def __init__(self, database: Database) -> None:
        self.database = database

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.database.session() as session:
            data["session"] = session
            telegram_user = data.get("event_from_user")
            if telegram_user:
                data["db_user"] = await UserRepository(session).upsert_telegram_user(telegram_user)
                await session.commit()
            return await handler(event, data)
