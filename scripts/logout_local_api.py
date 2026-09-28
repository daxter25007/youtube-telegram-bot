from __future__ import annotations

import asyncio
import os
import sys

import aiohttp


async def logout() -> int:
    token = os.getenv("BOT_TOKEN", "").strip()
    base_url = os.getenv("TELEGRAM_LOCAL_API_URL", "").strip().rstrip("/")
    if not token or not base_url:
        print("BOT_TOKEN and TELEGRAM_LOCAL_API_URL must be set in the environment.")
        return 2
    if "api.telegram.org" in base_url.lower():
        print("Refusing to use the cloud endpoint as TELEGRAM_LOCAL_API_URL.")
        return 2

    print(
        "This will log the bot out from the current Local Bot API Server. "
        "Stop the bot container first."
    )
    confirmation = await asyncio.to_thread(input, "Type CLOUD to continue: ")
    if confirmation.strip() != "CLOUD":
        print("Cancelled.")
        return 1

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(f"{base_url}/bot{token}/logOut") as response,
        ):
            payload = await response.json(content_type=None)
            status = response.status
    except (aiohttp.ClientError, TimeoutError) as exc:
        print(f"Local Bot API request failed: {type(exc).__name__}")
        return 3
    if status == 200 and payload.get("ok") and payload.get("result") is True:
        print("Success. Stop the Local Bot API Server before enabling the cloud endpoint.")
        return 0
    print(f"logOut failed (HTTP {status}): {payload.get('description', 'unknown error')}")
    return 4


if __name__ == "__main__":
    sys.exit(asyncio.run(logout()))
