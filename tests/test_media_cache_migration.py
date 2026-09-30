import importlib.util
from pathlib import Path
from types import ModuleType


def load_migration() -> ModuleType:
    path = Path("alembic/versions/20260930_0004_add_media_cache.py")
    assert path.exists()
    spec = importlib.util.spec_from_file_location("media_cache_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def add_column(self, table: str, column: object) -> None:
        self.calls.append(
            (
                "add_column",
                (table, getattr(column, "name", None), getattr(column, "nullable", None)),
            )
        )

    def create_table(self, table: str, *items: object) -> None:
        self.calls.append(
            (
                "create_table",
                (table, tuple(getattr(item, "name", None) for item in items)),
            )
        )

    def create_index(self, name: str, table: str, columns: list[str]) -> None:
        self.calls.append(("create_index", (name, table, tuple(columns))))

    def drop_index(self, name: str, table_name: str) -> None:
        self.calls.append(("drop_index", (name, table_name)))

    def drop_table(self, table: str) -> None:
        self.calls.append(("drop_table", table))

    def drop_column(self, table: str, column: str) -> None:
        self.calls.append(("drop_column", (table, column)))


def test_media_cache_migration_adds_cache_table_and_download_marker(monkeypatch) -> None:
    migration = load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert ("add_column", ("downloads", "cache_hit", False)) in operations.calls
    created = next(value for name, value in operations.calls if name == "create_table")
    assert created[0] == "media_cache"
    assert {
        "source",
        "source_media_id",
        "media_kind",
        "requested_option",
        "telegram_file_id",
        "telegram_file_unique_id",
        "sent_as_document",
        "actual_quality",
        "file_size",
        "last_used_at",
    }.issubset(set(created[1]))


def test_media_cache_migration_downgrade_removes_cache_schema(monkeypatch) -> None:
    migration = load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert ("drop_table", "media_cache") in operations.calls
    assert ("drop_column", ("downloads", "cache_hit")) in operations.calls
