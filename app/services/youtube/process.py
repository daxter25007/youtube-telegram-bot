from __future__ import annotations

import asyncio
import os
import signal


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix" and process.pid:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
    except (TimeoutError, ProcessLookupError):
        if process.returncode is None:
            try:
                if os.name == "posix" and process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


def subprocess_options() -> dict[str, bool]:
    return {"start_new_session": True} if os.name == "posix" else {}
