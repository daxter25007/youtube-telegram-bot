from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.youtube.formats import build_format_selector
from app.services.youtube.process import stop_process, subprocess_options

ProgressCallback = Callable[[str, float | None], Awaitable[None]]
PROGRESS_RE = re.compile(r"__PROGRESS__\s*([0-9]+(?:\.[0-9]+)?)%")
FILE_PREFIX = "__FILE__"


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    path: Path
    width: int | None
    height: int | None
    duration: int | None


class YouTubeDownloader:
    def __init__(self, cookies_file: str | None = None) -> None:
        self.cookies_file = cookies_file

    async def download(
        self,
        *,
        url: str,
        quality: int,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> DownloadedMedia:
        resolved_job_dir = await asyncio.to_thread(job_dir.resolve)
        output_template = str(job_dir / "media.%(ext)s")
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
            "mp4",
            "--remux-video",
            "mp4",
            "--js-runtimes",
            "node",
            "--socket-timeout",
            "30",
            "--retries",
            "5",
            "--fragment-retries",
            "5",
            "--format",
            build_format_selector(quality),
            "--output",
            output_template,
        ]
        if self.cookies_file:
            command.extend(["--cookies", self.cookies_file])
        command.extend(["--", url])
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
            raise DownloadError("\n".join(tail)[-2000:])
        if final_path is None:

            def find_final_file() -> Path | None:
                candidates = [
                    item
                    for item in job_dir.glob("media.*")
                    if item.is_file() and not item.name.endswith((".part", ".ytdl"))
                ]
                return max(candidates, key=lambda item: item.stat().st_mtime, default=None)

            final_path = await asyncio.to_thread(find_final_file)
        if final_path is None or not final_path.is_file():
            raise DownloadError("yt-dlp completed without a final media file")
        probe = await self._probe(final_path)
        return DownloadedMedia(
            path=final_path,
            width=probe.get("width"),
            height=probe.get("height"),
            duration=probe.get("duration"),
        )

    async def _probe(self, path: Path) -> dict[str, int | None]:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **subprocess_options(),
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.CancelledError:
            await stop_process(process)
            raise
        except TimeoutError:
            await stop_process(process)
            return {"width": None, "height": None, "duration": None}
        if process.returncode != 0:
            return {"width": None, "height": None, "duration": None}
        try:
            payload = json.loads(stdout)
            stream = (payload.get("streams") or [{}])[0]
            duration = (payload.get("format") or {}).get("duration")
            return {
                "width": int(stream["width"]) if stream.get("width") else None,
                "height": int(stream["height"]) if stream.get("height") else None,
                "duration": int(float(duration)) if duration else None,
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            return {"width": None, "height": None, "duration": None}
