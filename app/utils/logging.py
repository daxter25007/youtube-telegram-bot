from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

TOKEN_RE = re.compile(r"(?i)(?:bot)?\d{5,}:[A-Za-z0-9_-]{20,}")


class RedactingFormatter(logging.Formatter):
    def __init__(self, *args: object, secrets: Iterable[str] = (), **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._secrets = tuple(secret for secret in secrets if len(secret) >= 4)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        rendered = TOKEN_RE.sub("[REDACTED_TOKEN]", rendered)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def configure_logging(
    level: str,
    secrets: Iterable[str],
    *,
    log_dir: Path,
    max_bytes: int,
    backup_count: int,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "bot.log"
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        secrets=secrets,
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(level)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    return log_path


def recent_log_counts(log_path: Path, *, limit: int = 500) -> tuple[int, int]:
    if not log_path.exists():
        return 0, 0
    with log_path.open(encoding="utf-8", errors="replace") as file:
        lines = deque(file, maxlen=limit)
    warnings = sum(" WARNING " in line for line in lines)
    errors = sum(
        " ERROR " in line or " CRITICAL " in line or "Traceback (most recent call last)" in line
        for line in lines
    )
    return warnings, errors
