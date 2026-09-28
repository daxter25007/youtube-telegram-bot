import pytest
from pydantic import ValidationError

from app.config import Settings


def valid_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:abcdefghijklmnopqrstuvwxyz",
        "telegram_api_id": 123,
        "telegram_api_hash": "abcdef",
        "database_url": "postgresql+asyncpg://user:pass@postgres/db",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_settings_parse_admin_ids_and_local_api() -> None:
    settings = valid_settings(admin_ids="1, 2")
    assert settings.admin_ids == {1, 2}
    assert settings.telegram_local_api_url == "http://telegram-bot-api:8081"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bot_token", "not-a-token"),
        ("database_url", "sqlite:///file.db"),
        (
            "database_url",
            "postgresql+asyncpg://user:change-me-before-production@postgres/db",
        ),
        ("telegram_local_api_url", "https://api.telegram.org"),
    ],
)
def test_settings_reject_unsafe_or_invalid_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        valid_settings(**{field: value})
