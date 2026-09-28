from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RequiredChannel


class ChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active(self) -> list[RequiredChannel]:
        result = await self.session.scalars(
            select(RequiredChannel)
            .where(RequiredChannel.is_active.is_(True))
            .order_by(RequiredChannel.id)
        )
        return list(result)

    async def all(self) -> list[RequiredChannel]:
        result = await self.session.scalars(select(RequiredChannel).order_by(RequiredChannel.id))
        return list(result)

    async def add(
        self,
        *,
        chat_id: int,
        username: str | None,
        title: str,
        invite_url: str,
    ) -> RequiredChannel:
        existing = await self.session.scalar(
            select(RequiredChannel).where(RequiredChannel.chat_id == chat_id)
        )
        if existing:
            existing.username = username
            existing.title = title
            existing.invite_url = invite_url
            existing.is_active = True
            await self.session.flush()
            return existing
        channel = RequiredChannel(
            chat_id=chat_id,
            username=username,
            title=title,
            invite_url=invite_url,
        )
        self.session.add(channel)
        await self.session.flush()
        return channel

    async def toggle(self, channel_id: int) -> RequiredChannel | None:
        channel = await self.session.get(RequiredChannel, channel_id)
        if channel:
            channel.is_active = not channel.is_active
            await self.session.flush()
        return channel

    async def delete(self, channel_id: int) -> None:
        await self.session.execute(delete(RequiredChannel).where(RequiredChannel.id == channel_id))
