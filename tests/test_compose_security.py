import re
from pathlib import Path


def _service_block(name: str) -> str:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|^networks:)",
        compose,
    )
    assert match is not None
    return match.group("body")


def test_cobalt_service_is_pinned_internal_and_hardened() -> None:
    block = _service_block("cobalt")

    assert "ghcr.io/imputnet/cobalt:11.7.1-a636575@sha256:" in block
    assert "expose:" in block
    assert '"9000"' in block
    assert "ports:" not in block
    assert "privileged:" not in block
    assert "/var/run/docker.sock" not in block
    assert "read_only: true" in block
    assert "healthcheck:" in block


def test_bot_does_not_require_cobalt_health_to_start() -> None:
    block = _service_block("bot")

    assert "cobalt:" not in block


def test_bot_persists_logs_and_uses_fresh_heartbeat_healthcheck() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    block = _service_block("bot")

    assert "bot-logs:/app/logs" in block
    assert "bot-logs:" in compose
    assert "p.stat().st_mtime > 60" in block
    assert "max-size:" in compose
    assert "max-file:" in compose


def test_log_viewer_is_loopback_only_and_cannot_modify_logs() -> None:
    block = _service_block("log-viewer")

    assert '"127.0.0.1:${LOG_VIEWER_PORT:-8088}:8080"' in block
    assert "bot-logs:/app/logs:ro" in block
    assert "read_only: true" in block
    assert "no-new-privileges:true" in block
    assert "cap_drop:" in block
    assert "/var/run/docker.sock" not in block
    assert "env_file:" not in block
