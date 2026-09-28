from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import User as TelegramUser
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_telegram_user(self, telegram_user: TelegramUser) -> User:
        now = datetime.now(UTC)
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            statement = (
                pg_insert(User)
                .values(
                    telegram_id=telegram_user.id,
                    username=telegram_user.username,
                    first_name=telegram_user.first_name,
                    last_name=telegram_user.last_name,
                    last_seen_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[User.telegram_id],
                    set_={
                        "username": telegram_user.username,
                        "first_name": telegram_user.first_name,
                        "last_name": telegram_user.last_name,
                        "last_seen_at": now,
                    },
                )
                .returning(User)
            )
            user = await self.session.scalar(statement)
            if user is None:
                raise RuntimeError("User upsert returned no row")
            return user
        user = await self.get_by_telegram_id(telegram_user.id)
        if user is None:
            user = User(
                telegram_id=telegram_user.id,
                username=telegram_user.username,
                first_name=telegram_user.first_name,
                last_name=telegram_user.last_name,
            )
            self.session.add(user)
            await self.session.flush()
            return user
        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
        user.last_name = telegram_user.last_name
        user.last_seen_at = now
        await self.session.flush()
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def increment_downloads(self, user_id: int) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(downloads_count=User.downloads_count + 1)
        )

    async def recent(self, limit: int = 20) -> list[User]:
        result = await self.session.scalars(
            select(User).order_by(User.created_at.desc()).limit(limit)
        )
        return list(result)
