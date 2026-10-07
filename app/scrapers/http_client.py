"""Factory for the shared `httpx.AsyncClient` used to talk to Dia.

Akamai Bot Manager answers 403 to a Chrome User-Agent that comes without the
headers a real Chrome sends (client hints or `Sec-Fetch-*`, Fase 0 §5), so the
client sends one coherent Chrome profile on every request (spec 001 RF-4,
plan-D12). The version is Alcampo's, verified on 2026-09-25 against Chrome's
release API and accepted by Dia on 2026-10-07. Review it roughly every 3
months together with Alcampo's pool: an outdated User-Agent is itself a bot signal.
"""

from types import MappingProxyType

import httpx

from app.core.config import Settings

CHROME_VERSION = "155"

# The User-Agent and the client hints must name the same version, or the pair
# gives the bot away.
CHROME_HEADERS = MappingProxyType(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": (
            f'"Chromium";v="{CHROME_VERSION}", "Google Chrome";v="{CHROME_VERSION}", '
            '"Not.A/Brand";v="99"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        # What Dia's own web client sends on its API calls (a same-origin fetch).
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        # Constant on purpose: it imitates the browser, not our config. Deriving it
        # from DIA_BASE_URL would leak a fake Referer behind a proxy (plan-D12).
        "Referer": "https://www.dia.es/",
    }
)


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """Build the single `httpx.AsyncClient` used for the lifetime of the app."""
    return httpx.AsyncClient(
        base_url=settings.dia_base_url,
        timeout=settings.http_timeout_seconds,
        headers=CHROME_HEADERS,
    )
