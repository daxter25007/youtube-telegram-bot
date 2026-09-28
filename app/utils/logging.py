from __future__ import annotations

import logging
import re
from collections.abc import Iterable

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


def configure_logging(level: str, secrets: Iterable[str]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            secrets=secrets,
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
