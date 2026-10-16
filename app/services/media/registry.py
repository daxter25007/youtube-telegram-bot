from __future__ import annotations

from collections.abc import Iterable

from app.services.media.adapters.base import MediaAdapter
from app.services.media.adapters.instagram import InstagramAdapter
from app.services.media.adapters.pinterest import PinterestAdapter
from app.services.media.adapters.rutube import RutubeAdapter
from app.services.media.adapters.tiktok import TikTokAdapter
from app.services.media.adapters.vk import VKAdapter
from app.services.media.adapters.youtube import YouTubeAdapter
from app.services.media.models import MediaSource, ParsedMediaUrl


class MediaRegistry:
    def __init__(self, adapters: Iterable[MediaAdapter]) -> None:
        self._adapters = {adapter.source: adapter for adapter in adapters}

    @classmethod
    def default(cls) -> MediaRegistry:
        return cls(
            (
                YouTubeAdapter(),
                RutubeAdapter(),
                TikTokAdapter(),
                InstagramAdapter(),
                PinterestAdapter(),
                VKAdapter(),
            )
        )

    def parse(self, text: str) -> ParsedMediaUrl | None:
        for adapter in self._adapters.values():
            if parsed := adapter.parse_url(text):
                return parsed
        return None

    def adapter_for(self, source: MediaSource) -> MediaAdapter:
        return self._adapters[source]
