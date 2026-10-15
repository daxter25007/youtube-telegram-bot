from pathlib import Path

import pytest

from app.log_viewer import INDEX_HTML, available_log_files, read_log_tail


def test_log_viewer_lists_current_and_rotated_logs(tmp_path: Path) -> None:
    for name in ("bot.log.2", "bot.log", "bot.log.1", "other.log"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    assert available_log_files(tmp_path) == ["bot.log", "bot.log.1", "bot.log.2"]


def test_log_viewer_reads_only_requested_tail(tmp_path: Path) -> None:
    (tmp_path / "bot.log").write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert read_log_tail(tmp_path, "bot.log", 2) == ["two", "three"]


def test_log_viewer_rejects_directory_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Недопустимое"):
        read_log_tail(tmp_path, "../.env", 100)


def test_log_viewer_page_has_filters_and_safe_text_rendering() -> None:
    assert "Поиск по журналу" in INDEX_HTML
    assert "Все уровни" in INDEX_HTML
    assert "textContent" in INDEX_HTML
    assert "shown.join(String.fromCharCode(10))" in INDEX_HTML
    assert "innerHTML" not in INDEX_HTML
