from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.media_manifest import MediaManifest
from app.services.media.models import MediaPart, MediaSource


class MediaManifestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self,
        *,
        source: MediaSource,
        source_media_id: str,
    ) -> MediaManifest | None:
        return await self.session.scalar(
            select(MediaManifest).where(
                MediaManifest.source == source,
                MediaManifest.source_media_id == source_media_id,
            )
        )

    async def upsert(
        self,
        *,
        source: MediaSource,
        source_media_id: str,
        parts: tuple[MediaPart, ...],
    ) -> MediaManifest:
        values = {
            "source": source,
            "source_media_id": source_media_id,
            "parts_json": [part.as_dict() for part in parts],
        }
        dialect_name = self.session.get_bind().dialect.name
        insert_factory = {
            "postgresql": postgresql_insert,
            "sqlite": sqlite_insert,
        }.get(dialect_name)
        if insert_factory is not None:
            statement = insert_factory(MediaManifest).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[MediaManifest.source, MediaManifest.source_media_id],
                set_={"parts_json": statement.excluded.parts_json},
            )
            await self.session.execute(statement)
            await self.session.flush()
        else:
            item = await self.get(source=source, source_media_id=source_media_id)
            if item is None:
                self.session.add(MediaManifest(**values))
            else:
                item.parts_json = values["parts_json"]
            await self.session.flush()
        item = await self.get(source=source, source_media_id=source_media_id)
        if item is None:
            raise RuntimeError("Не удалось сохранить манифест медиа")
        self.session.expire(item, ["parts_json"])
        await self.session.refresh(item, ["parts_json"])
        return item
