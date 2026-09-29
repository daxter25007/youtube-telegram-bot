from pathlib import Path

import pytest

from app.services.media.downloader import (
    DownloadError,
    MediaDownloader,
    build_download_command,
)
from app.services.media.models import DownloadPolicy, MediaKind


def test_download_command_places_url_after_option_separator(tmp_path: Path) -> None:
    url = "https://www.tiktok.com/@creator/video/7461234567890123456"
    policy = DownloadPolicy(format_selector="best[ext=mp4]/best")

    command = build_download_command(
        url=url,
        policy=policy,
        output_template=str(tmp_path / "media.%(ext)s"),
        cookies_file=None,
    )

    assert command[-2:] == ["--", url]
    assert command[command.index("--format") + 1] == "best[ext=mp4]/best"
    assert "shell=True" not in command


class FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class FakeProcess:
    def __init__(self, lines: list[bytes], return_code: int = 0) -> None:
        self.stdout = FakeStream(lines)
        self.return_code = return_code

    async def wait(self) -> int:
        return self.return_code


@pytest.mark.asyncio
async def test_downloader_returns_video_package_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "media.mp4"
    output.write_bytes(b"video")
    process = FakeProcess(
        [
            b"__PROGRESS__ 37.5%\n",
            b"__POSTPROCESS__started\n",
            f"__FILE__{output}\n".encode(),
        ]
    )

    async def fake_subprocess(*args: object, **kwargs: object) -> FakeProcess:
        return process

    async def fake_probe(self: MediaDownloader, path: Path) -> dict[str, int | None]:
        return {"width": 1080, "height": 1920, "duration": 15}

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(MediaDownloader, "_probe", fake_probe)
    progress: list[tuple[str, float | None]] = []

    async def record(stage: str, percent: float | None) -> None:
        progress.append((stage, percent))

    package = await MediaDownloader().download(
        url="https://vm.tiktok.com/ZM123abc/",
        policy=DownloadPolicy(format_selector="best[ext=mp4]/best"),
        job_dir=tmp_path,
        progress=record,
    )

    assert progress == [("downloading", 37.5), ("merging", None)]
    assert len(package.artifacts) == 1
    artifact = package.artifacts[0]
    assert artifact.path == output
    assert artifact.media_kind is MediaKind.VIDEO
    assert (artifact.width, artifact.height, artifact.duration) == (1080, 1920, 15)


@pytest.mark.asyncio
async def test_downloader_rejects_reported_path_outside_job_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"video")
    process = FakeProcess([f"__FILE__{outside}\n".encode()])

    async def fake_subprocess(*args: object, **kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)

    async def ignore_progress(stage: str, percent: float | None) -> None:
        return None

    with pytest.raises(DownloadError, match="job"):
        await MediaDownloader().download(
            url="https://vm.tiktok.com/ZM123abc/",
            policy=DownloadPolicy(format_selector="best"),
            job_dir=tmp_path,
            progress=ignore_progress,
        )
