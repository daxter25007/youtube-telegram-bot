from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr
    telegram_api_id: int
    telegram_api_hash: SecretStr
    telegram_api_mode: Literal["local", "cloud"] = "local"
    telegram_local_api_url: str = "http://telegram-bot-api:8081"
    admin_ids: frozenset[int] = Field(default_factory=frozenset)

    database_url: SecretStr

    max_upload_mb: int = Field(default=1950, gt=0, lt=2000)
    max_concurrent_downloads: int = Field(default=3, ge=1, le=32)
    max_downloads_per_user: int = Field(default=1, ge=1, le=10)
    download_timeout_seconds: int = Field(default=1800, ge=30)
    metadata_timeout_seconds: int = Field(default=120, ge=10)

    temp_root: Path = Path("/shared-temp/jobs")
    temp_file_ttl_hours: int = Field(default=6, ge=1)
    cleanup_interval_seconds: int = Field(default=3600, ge=60)

    rate_limit_urls: int = Field(default=5, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    download_request_ttl_minutes: int = Field(default=30, ge=1)
    progress_update_seconds: float = Field(default=3.0, ge=1.0)

    log_level: str = "INFO"
    ytdlp_cookies_file: Path | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> frozenset[int]:
        if value in (None, ""):
            return frozenset()
        if isinstance(value, str):
            try:
                return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
            except ValueError as exc:
                raise ValueError("ADMIN_IDS must contain only comma-separated numeric IDs") from exc
        if isinstance(value, bool):
            raise ValueError("ADMIN_IDS must contain only numeric IDs")
        if isinstance(value, int):
            return frozenset({value})
        return frozenset(value)  # type: ignore[arg-type]

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        prefix, separator, secret = token.partition(":")
        if not separator or not prefix.isdigit() or len(secret) < 20:
            raise ValueError("BOT_TOKEN has an invalid format")
        return value

    @field_validator("telegram_api_hash")
    @classmethod
    def validate_api_hash(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("TELEGRAM_API_HASH cannot be empty")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        database_url = value.get_secret_value()
        if not database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        if "change-me-before-production" in database_url:
            raise ValueError("Replace the example PostgreSQL password")
        return value

    @field_validator("telegram_local_api_url")
    @classmethod
    def normalize_api_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is invalid")
        return level

    @field_validator("ytdlp_cookies_file", mode="before")
    @classmethod
    def empty_cookie_path_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_local_mode(self) -> Settings:
        if self.telegram_api_mode == "local":
            if "api.telegram.org" in self.telegram_local_api_url.lower():
                raise ValueError("Local mode cannot use api.telegram.org")
            if not self.telegram_local_api_url.startswith(("http://", "https://")):
                raise ValueError("TELEGRAM_LOCAL_API_URL must be an HTTP(S) URL")
        if self.ytdlp_cookies_file and not self.ytdlp_cookies_file.is_absolute():
            raise ValueError("YTDLP_COOKIES_FILE must be an absolute runtime-mounted path")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def request_ttl_seconds(self) -> int:
        return self.download_request_ttl_minutes * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
