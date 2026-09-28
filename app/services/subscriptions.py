from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError

from app.database.models import RequiredChannel

logger = logging.getLogger(__name__)
SUBSCRIBED_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
}


class SubscriptionService:
    async def missing_channels(
        self,
        bot: Bot,
        user_id: int,
        channels: list[RequiredChannel],
    ) -> list[RequiredChannel]:
        missing: list[RequiredChannel] = []
        for channel in channels:
            try:
                member = await bot.get_chat_member(channel.chat_id, user_id)
                if member.status not in SUBSCRIBED_STATUSES:
                    missing.append(channel)
            except TelegramAPIError as exc:
                logger.warning("Subscription check failed for channel_id=%s: %s", channel.id, exc)
                missing.append(channel)
        return missing
