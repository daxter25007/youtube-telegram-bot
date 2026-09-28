from __future__ import annotations

import asyncio
import os
import sys

import aiohttp


async def migrate() -> int:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        print("BOT_TOKEN is not set in the environment.")
        return 2

    print(
        "This will call logOut on the Telegram cloud Bot API. "
        "The bot cannot return to the cloud API for 10 minutes."
    )
    confirmation = await asyncio.to_thread(input, "Type LOCAL to continue: ")
    if confirmation.strip() != "LOCAL":
        print("Cancelled.")
        return 1

    timeout = aiohttp.ClientTimeout(total=30)
    url = f"https://api.telegram.org/bot{token}/logOut"
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(url) as response,
        ):
            payload = await response.json(content_type=None)
            status = response.status
    except (aiohttp.ClientError, TimeoutError) as exc:
        print(f"Cloud Bot API request failed: {type(exc).__name__}")
        return 3

    if status == 200 and payload.get("ok") and payload.get("result") is True:
        print("Success. Start the local Bot API server now.")
        return 0

    print(f"logOut failed (HTTP {status}): {payload.get('description', 'unknown error')}")
    return 4


if __name__ == "__main__":
    sys.exit(asyncio.run(migrate()))
