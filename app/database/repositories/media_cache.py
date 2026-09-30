from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.media_cache import MediaCache
from app.services.media.models import MediaKind, MediaSource


class MediaCacheRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self,
        *,
        source: MediaSource,
        source_media_id: str,
        media_kind: MediaKind,
        requested_option: str,
    ) -> MediaCache | None:
        return await self.session.scalar(
            select(MediaCache).where(
                MediaCache.source == source,
                MediaCache.source_media_id == source_media_id,
                MediaCache.media_kind == media_kind,
                MediaCache.requested_option == requested_option,
            )
        )

    async def store(
        self,
        *,
        source: MediaSource,
        source_media_id: str,
        media_kind: MediaKind,
        requested_option: str,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        sent_as_document: bool,
        actual_quality: int | None,
        file_size: int | None,
    ) -> MediaCache:
        values = {
            "source": source,
            "source_media_id": source_media_id,
            "media_kind": media_kind,
            "requested_option": requested_option,
            "telegram_file_id": telegram_file_id,
            "telegram_file_unique_id": telegram_file_unique_id,
            "sent_as_document": sent_as_document,
            "actual_quality": actual_quality,
            "file_size": file_size,
        }
        dialect_name = self.session.get_bind().dialect.name
        insert_factory = {
            "postgresql": postgresql_insert,
            "sqlite": sqlite_insert,
        }.get(dialect_name)
        if insert_factory is not None:
            statement = insert_factory(MediaCache).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    MediaCache.source,
                    MediaCache.source_media_id,
                    MediaCache.media_kind,
                    MediaCache.requested_option,
                ],
                set_={
                    "telegram_file_id": statement.excluded.telegram_file_id,
                    "telegram_file_unique_id": statement.excluded.telegram_file_unique_id,
                    "sent_as_document": statement.excluded.sent_as_document,
                    "actual_quality": statement.excluded.actual_quality,
                    "file_size": statement.excluded.file_size,
                    "last_used_at": datetime.now(UTC),
                },
            )
            await self.session.execute(statement)
            await self.session.flush()
            item = await self.get(
                source=source,
                source_media_id=source_media_id,
                media_kind=media_kind,
                requested_option=requested_option,
            )
            if item is None:
                raise RuntimeError("Не удалось сохранить Telegram-кэш")
            return item

        item = await self.get(
            source=source,
            source_media_id=source_media_id,
            media_kind=media_kind,
            requested_option=requested_option,
        )
        if item is None:
            item = MediaCache(
                **values,
            )
            self.session.add(item)
        else:
            item.telegram_file_id = telegram_file_id
            item.telegram_file_unique_id = telegram_file_unique_id
            item.sent_as_document = sent_as_document
            item.actual_quality = actual_quality
            item.file_size = file_size
            item.last_used_at = datetime.now(UTC)
        await self.session.flush()
        return item

    async def cached_option_keys(
        self,
        *,
        source: MediaSource,
        source_media_id: str,
        media_kind: MediaKind,
    ) -> set[str]:
        result = await self.session.scalars(
            select(MediaCache.requested_option).where(
                MediaCache.source == source,
                MediaCache.source_media_id == source_media_id,
                MediaCache.media_kind == media_kind,
            )
        )
        return set(result)

    async def delete(self, item_id: int) -> None:
        await self.session.execute(delete(MediaCache).where(MediaCache.id == item_id))

    async def touch(self, item_id: int) -> None:
        await self.session.execute(
            update(MediaCache)
            .where(MediaCache.id == item_id)
            .values(last_used_at=datetime.now(UTC))
        )
