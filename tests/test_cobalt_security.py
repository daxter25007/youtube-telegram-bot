import pytest

from app.services.cobalt.security import SafeRemoteUrlValidator, UnsafeRemoteUrlError


def resolver_for(*addresses: str):
    async def resolve(host: str, port: int) -> tuple[str, ...]:
        return addresses

    return resolve


@pytest.mark.asyncio
async def test_validator_allows_configured_internal_cobalt_origin() -> None:
    validator = SafeRemoteUrlValidator(resolver_for("127.0.0.1"))

    result = await validator.validate(
        "http://cobalt:9000/tunnel?id=secret",
        allowed_internal_origin="http://cobalt:9000",
    )

    assert result.is_internal is True
    assert result.hostname == "cobalt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/video.mp4",
        "http://10.1.2.3/video.mp4",
        "http://169.254.1.1/video.mp4",
        "http://[::1]/video.mp4",
        "http://user:password@example.com/video.mp4",
        "ftp://example.com/video.mp4",
        "https://example.com:8443/video.mp4",
        "https://example.com/video.mp4#fragment",
    ],
)
async def test_validator_rejects_unsafe_remote_url(url: str) -> None:
    validator = SafeRemoteUrlValidator(resolver_for("93.184.216.34"))

    with pytest.raises(UnsafeRemoteUrlError):
        await validator.validate(url)


@pytest.mark.asyncio
async def test_validator_returns_pinned_public_addresses() -> None:
    validator = SafeRemoteUrlValidator(resolver_for("93.184.216.34", "2606:2800:220:1::1"))

    result = await validator.validate("https://cdn.example.com/video.mp4?token=secret")

    assert result.is_internal is False
    assert result.addresses == ("93.184.216.34", "2606:2800:220:1::1")
    assert result.port == 443


@pytest.mark.asyncio
async def test_validator_rechecks_redirect_target_and_blocks_private_ip() -> None:
    public = SafeRemoteUrlValidator(resolver_for("93.184.216.34"))
    private = SafeRemoteUrlValidator(resolver_for("192.168.1.20"))

    await public.validate("https://cdn.example.com/video.mp4")
    with pytest.raises(UnsafeRemoteUrlError):
        await private.validate("https://redirect.example.com/video.mp4")
