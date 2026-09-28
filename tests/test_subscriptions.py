from types import SimpleNamespace

import pytest
from aiogram.enums import ChatMemberStatus

from app.database.models import RequiredChannel
from app.services.subscriptions import SubscriptionService


class FakeBot:
    def __init__(self, statuses: dict[int, ChatMemberStatus]) -> None:
        self.statuses = statuses

    async def get_chat_member(self, chat_id: int, user_id: int):
        return SimpleNamespace(status=self.statuses[chat_id])


@pytest.mark.asyncio
async def test_subscription_statuses() -> None:
    channels = [
        RequiredChannel(id=1, chat_id=-1, title="one", invite_url="https://t.me/one"),
        RequiredChannel(id=2, chat_id=-2, title="two", invite_url="https://t.me/two"),
    ]
    bot = FakeBot({-1: ChatMemberStatus.MEMBER, -2: ChatMemberStatus.LEFT})
    missing = await SubscriptionService().missing_channels(bot, 123, channels)  # type: ignore[arg-type]
    assert [channel.id for channel in missing] == [2]
