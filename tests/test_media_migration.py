import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa


def load_migration() -> ModuleType:
    path = Path("alembic/versions/20260929_0002_generalize_media_sources.py")
    spec = importlib.util.spec_from_file_location("media_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def alter_column(self, table: str, column: str, **kwargs: object) -> None:
        self.calls.append(("alter_column", (table, column, kwargs)))

    def add_column(self, table: str, column: object) -> None:
        self.calls.append(("add_column", (table, getattr(column, "name", None))))

    def drop_column(self, table: str, column: str) -> None:
        self.calls.append(("drop_column", (table, column)))

    def execute(self, statement: object) -> None:
        self.calls.append(("execute", str(statement)))


def test_media_migration_renames_and_backfills_without_losing_youtube_rows(
    monkeypatch,
) -> None:
    migration = load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert (
        "alter_column",
        ("downloads", "youtube_url", {"new_column_name": "source_url"}),
    ) in operations.calls
    assert (
        "alter_column",
        ("download_requests", "youtube_video_id", {"new_column_name": "source_media_id"}),
    ) in operations.calls
    executed = "\n".join(str(value) for name, value in operations.calls if name == "execute")
    assert "source = 'youtube'" in executed
    assert "media_kind = 'video'" in executed
    assert "video_" in executed
    assert any(
        name == "alter_column"
        and value[0] == "downloads"
        and value[1] == "source_media_id"
        and isinstance(value[2].get("type_"), sa.String)
        and value[2]["type_"].length == 128
        for name, value in operations.calls
    )


def test_media_migration_downgrade_restores_youtube_column_names(monkeypatch) -> None:
    migration = load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert (
        "alter_column",
        ("downloads", "source_url", {"new_column_name": "youtube_url"}),
    ) in operations.calls
    assert (
        "alter_column",
        ("download_requests", "source_media_id", {"new_column_name": "youtube_video_id"}),
    ) in operations.calls
