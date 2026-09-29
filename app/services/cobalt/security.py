from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

AddressResolver = Callable[[str, int], Awaitable[Sequence[str]]]


class UnsafeRemoteUrlError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValidatedRemoteUrl:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]
    is_internal: bool


def _origin(parts: SplitResult) -> tuple[str, str, int]:
    if not parts.hostname:
        raise UnsafeRemoteUrlError("URL не содержит hostname")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeRemoteUrlError("URL содержит некорректный порт") from exc
    return parts.scheme, parts.hostname.rstrip(".").lower(), port


async def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


class SafeRemoteUrlValidator:
    def __init__(self, resolver: AddressResolver = _default_resolver) -> None:
        self._resolver = resolver

    async def validate(
        self,
        url: str,
        *,
        allowed_internal_origin: str | None = None,
    ) -> ValidatedRemoteUrl:
        if not url or any(character.isspace() for character in url):
            raise UnsafeRemoteUrlError("URL пуст или содержит пробелы")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise UnsafeRemoteUrlError("Разрешены только HTTP(S) URL")
        if parts.username is not None or parts.password is not None:
            raise UnsafeRemoteUrlError("Credentials в URL запрещены")
        if parts.fragment:
            raise UnsafeRemoteUrlError("Fragment в URL запрещён")
        scheme, hostname, port = _origin(parts)

        if allowed_internal_origin is not None:
            allowed_parts = urlsplit(allowed_internal_origin)
            if (scheme, hostname, port) == _origin(allowed_parts):
                return ValidatedRemoteUrl(
                    url=url,
                    hostname=hostname,
                    port=port,
                    addresses=(),
                    is_internal=True,
                )

        if port not in {80, 443}:
            raise UnsafeRemoteUrlError("Нестандартный внешний порт запрещён")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            addresses = tuple(await self._resolver(hostname, port))
        else:
            addresses = (str(literal),)
        if not addresses:
            raise UnsafeRemoteUrlError("Hostname не разрешился")
        try:
            parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
        except ValueError as exc:
            raise UnsafeRemoteUrlError("Resolver вернул некорректный IP") from exc
        if any(not address.is_global for address in parsed_addresses):
            raise UnsafeRemoteUrlError("Внутренний или зарезервированный IP запрещён")
        return ValidatedRemoteUrl(
            url=url,
            hostname=hostname,
            port=port,
            addresses=tuple(str(address) for address in parsed_addresses),
            is_internal=False,
        )
