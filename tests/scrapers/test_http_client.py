import re

import httpx

from app.core.config import Settings
from app.scrapers.http_client import create_http_client


def make_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "dia_base_url": "https://dia.test",
        "redis_url": "redis://localhost:6379/0",
        "http_timeout_seconds": 7.5,
    }
    return Settings.model_validate(fields | overrides)


async def test_client_uses_base_url_and_timeout() -> None:
    client = create_http_client(make_settings())
    try:
        assert isinstance(client, httpx.AsyncClient)
        assert client.base_url == httpx.URL("https://dia.test")
        assert client.timeout == httpx.Timeout(7.5)
    finally:
        await client.aclose()

    assert client.is_closed


async def test_client_sends_a_coherent_chrome_header_set() -> None:
    # Akamai answers 403 to a Chrome User-Agent without client hints or
    # Sec-Fetch-* headers (Fase 0 §5, spec 001 RF-4).
    client = create_http_client(make_settings())
    try:
        headers = client.headers
    finally:
        await client.aclose()

    assert "Chrome/155." in headers["User-Agent"]
    assert "Windows NT 10.0" in headers["User-Agent"]
    assert headers["Accept"] == "application/json, text/plain, */*"
    assert headers["Accept-Language"].startswith("es-ES")
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["sec-ch-ua-platform"] == '"Windows"'
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Sec-Fetch-Site"] == "same-origin"


async def test_client_hints_carry_the_same_chrome_version_as_the_user_agent() -> None:
    client = create_http_client(make_settings())
    try:
        headers = client.headers
    finally:
        await client.aclose()

    ua_version = re.search(r"Chrome/(\d+)\.", headers["User-Agent"])
    hint_version = re.search(r'"Google Chrome";v="(\d+)"', headers["sec-ch-ua"])
    assert ua_version is not None and hint_version is not None
    assert ua_version.group(1) == hint_version.group(1)


async def test_referer_is_dias_home_whatever_the_base_url() -> None:
    # It imitates the browser, not our config (plan-D12).
    client = create_http_client(make_settings(dia_base_url="http://proxy.internal:8080"))
    try:
        assert client.headers["Referer"] == "https://www.dia.es/"
    finally:
        await client.aclose()
