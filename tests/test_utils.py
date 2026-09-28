from app.utils.auth import is_admin
from app.utils.filenames import sanitize_display_filename
from app.utils.filesize import is_too_large


def test_filename_is_sanitized_and_bounded() -> None:
    filename = sanitize_display_filename("../bad/path\x00:name?" + "x" * 300)
    assert ".." not in filename
    assert "/" not in filename
    assert "\\" not in filename
    assert "\x00" not in filename
    assert filename.endswith(".mp4")
    assert len(filename) <= 120


def test_size_validation_uses_strict_greater_than() -> None:
    assert not is_too_large(100, 100)
    assert is_too_large(101, 100)


def test_admin_access_is_numeric_id_only() -> None:
    assert is_admin(123, {123, 456})
    assert not is_admin(789, {123, 456})
