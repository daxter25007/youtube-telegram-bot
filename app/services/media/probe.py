from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app.services.media.process import stop_process, subprocess_options


@dataclass(frozen=True, slots=True)
class MediaProbe:
    width: int | None
    height: int | None
    duration: int | None


async def probe_media(path: Path) -> MediaProbe:
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
        return MediaProbe(width=None, height=None, duration=None)
    if process.returncode != 0:
        return MediaProbe(width=None, height=None, duration=None)
    try:
        payload = json.loads(stdout)
        stream = (payload.get("streams") or [{}])[0]
        duration = (payload.get("format") or {}).get("duration")
        return MediaProbe(
            width=int(stream["width"]) if stream.get("width") else None,
            height=int(stream["height"]) if stream.get("height") else None,
            duration=int(float(duration)) if duration else None,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return MediaProbe(width=None, height=None, duration=None)
