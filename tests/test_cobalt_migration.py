import importlib.util
from pathlib import Path
from types import ModuleType


def load_migration() -> ModuleType:
    path = Path("alembic/versions/20260930_0003_add_download_backend.py")
    assert path.exists()
    spec = importlib.util.spec_from_file_location("cobalt_backend_migration", path)
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

    def drop_column(self, table: str, column: str) -> None:
        self.calls.append(("drop_column", (table, column)))

    def execute(self, statement: object) -> None:
        self.calls.append(("execute", str(statement)))


def test_cobalt_migration_adds_nullable_backend_and_backfills_completed(monkeypatch) -> None:
    migration = load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert ("add_column", ("downloads", "backend", True)) in operations.calls
    executed = "\n".join(str(value) for name, value in operations.calls if name == "execute")
    assert "status = 'completed'" in executed
    assert "backend = 'ytdlp'" in executed


def test_cobalt_migration_downgrade_drops_backend(monkeypatch) -> None:
    migration = load_migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    assert ("drop_column", ("downloads", "backend")) in operations.calls
