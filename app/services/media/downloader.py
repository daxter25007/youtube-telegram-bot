from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.services.media.models import (
    DownloadPolicy,
    MediaArtifact,
    MediaKind,
    MediaPackage,
)
from app.services.media.probe import probe_media
from app.services.media.process import stop_process, subprocess_options

ProgressCallback = Callable[[str, float | None], Awaitable[None]]
PROGRESS_RE = re.compile(r"__PROGRESS__\s*([0-9]+(?:\.[0-9]+)?)%")
FILE_PREFIX = "__FILE__"


class DownloadError(RuntimeError):
    pass


def build_download_command(
    *,
    url: str,
    policy: DownloadPolicy,
    output_template: str,
    cookies_file: str | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--newline",
        "--progress",
        "--progress-template",
        "download:__PROGRESS__%(progress._percent_str)s",
        "--progress-template",
        "postprocess:__POSTPROCESS__%(progress.status)s",
        "--print",
        "after_move:__FILE__%(filepath)s",
        "--merge-output-format",
        policy.merge_output_format,
        "--remux-video",
        policy.remux_video,
        "--js-runtimes",
        "node",
        "--socket-timeout",
        "30",
        "--retries",
        "5",
        "--fragment-retries",
        "5",
        "--format",
        policy.format_selector,
        "--output",
        output_template,
    ]
    if cookies_file:
        command.extend(["--cookies", cookies_file])
    command.extend(["--", url])
    return command


class MediaDownloader:
    def __init__(self, cookies_file: str | None = None) -> None:
        self.cookies_file = cookies_file

    async def download(
        self,
        *,
        url: str,
        policy: DownloadPolicy,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage:
        resolved_job_dir = await asyncio.to_thread(job_dir.resolve)
        command = build_download_command(
            url=url,
            policy=policy,
            output_template=str(job_dir / "media.%(ext)s"),
            cookies_file=self.cookies_file,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **subprocess_options(),
        )
        final_path: Path | None = None
        tail: list[str] = []
        try:
            assert process.stdout is not None
            while line_bytes := await process.stdout.readline():
                line = line_bytes.decode("utf-8", errors="replace").strip()
                tail.append(line)
                tail = tail[-20:]
                if match := PROGRESS_RE.search(line):
                    await progress("downloading", float(match.group(1)))
                elif (
                    "[Merger]" in line
                    or "[VideoRemuxer]" in line
                    or line.startswith("__POSTPROCESS__")
                ):
                    await progress("merging", None)
                elif line.startswith(FILE_PREFIX):
                    raw_candidate = Path(line.removeprefix(FILE_PREFIX))
                    candidate = await asyncio.to_thread(raw_candidate.resolve)
                    if candidate.parent == resolved_job_dir:
                        final_path = candidate
            return_code = await process.wait()
        except asyncio.CancelledError:
            await stop_process(process)
            raise
        if return_code != 0:
            raise DownloadError("\n".join(tail)[-2_000:])
        if final_path is None:
            final_path = await asyncio.to_thread(self._find_final_file, job_dir)
        if final_path is None or not final_path.is_file():
            raise DownloadError("yt-dlp завершился без файла внутри job-каталога")
        resolved_final_path = await asyncio.to_thread(final_path.resolve)
        if resolved_final_path.parent != resolved_job_dir:
            raise DownloadError("yt-dlp вернул файл вне job-каталога")
        probe = await self._probe(resolved_final_path)
        artifact = MediaArtifact(
            path=resolved_final_path,
            media_kind=MediaKind.VIDEO,
            width=probe.get("width"),
            height=probe.get("height"),
            duration=probe.get("duration"),
            mime_type="video/mp4" if resolved_final_path.suffix.lower() == ".mp4" else None,
        )
        return MediaPackage(artifacts=(artifact,))

    @staticmethod
    def _find_final_file(job_dir: Path) -> Path | None:
        candidates = [
            item
            for item in job_dir.glob("media.*")
            if item.is_file() and not item.name.endswith((".part", ".ytdl"))
        ]
        return max(candidates, key=lambda item: item.stat().st_mtime, default=None)

    async def _probe(self, path: Path) -> dict[str, int | None]:
        probe = await probe_media(path)
        return {
            "width": probe.width,
            "height": probe.height,
            "duration": probe.duration,
        }
