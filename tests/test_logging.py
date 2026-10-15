import logging
from pathlib import Path

from app.utils.logging import configure_logging, recent_log_counts


def test_file_logging_redacts_secrets_and_is_readable(tmp_path: Path) -> None:
    log_path = configure_logging(
        "INFO",
        secrets=("private-value",),
        log_dir=tmp_path,
        max_bytes=1024 * 1024,
        backup_count=2,
    )

    logging.getLogger("test").warning("secret=%s", "private-value")
    logging.getLogger("test").error("failure")
    for handler in logging.getLogger().handlers:
        handler.flush()

    logs = log_path.read_text(encoding="utf-8")
    assert "private-value" not in logs
    assert "[REDACTED]" in logs
    assert recent_log_counts(log_path) == (1, 1)
