from __future__ import annotations

import asyncio
import json
from typing import Optional

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.menus import (
    durationKeyboard, workersKeyboard, proxyKeyboard,
    confirmKeyboard, runningKeyboard, finishedKeyboard, mainMenuKeyboard,
)
from bot.services.tester_runner import TesterRunner, validateProxies
from bot.services.proxy_manager import proxyManager
from bot.services.database import db, IST
from bot.config import DASHBOARD_UPDATE_INTERVAL, ADMIN_ID, PROTECTED_NUMBER
from bot.utils import PM, b, i, c, hEsc

import random
from datetime import datetime

router = Router()

activeRunners:   dict = {}
dashboardTasks:  dict = {}
summaryShown:    dict = {}
activeRecordIds: dict = {}
_lastConfig:     dict = {}

PROTECTED_RESPONSES = [
    "Poda kunne onn!!!",
    "Onn poyeda vadhoori",
    "Ninta pari!",
    "Ntelekk ondaakaan varalletta myre, chethi kallayum panni ninta suna!!",
]


class TestWizard(StatesGroup):
    phone          = State()
    duration       = State()
    durationCustom = State()
    workers        = State()
    workersCustom  = State()
    proxy          = State()
    proxyChecking  = State()
    confirm        = State()
    running        = State()


def formatDuration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = seconds // 60, seconds % 60
        return f"{m}m {s}s" if s else f"{m}m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m}m" if m else f"{h}h"


def parseTime(t: str) -> Optional[int]:
    t = (t or "").strip()
    try:
        if t.endswith("s"): return int(t[:-1])
        if t.endswith("m"): return int(t[:-1]) * 60
        if t.endswith("h"): return int(t[:-1]) * 3600
        return int(t)
    except (ValueError, AttributeError):
        return None


def parsePhones(text: str) -> list:
    """Parse single or multiple comma-separated 10-digit numbers."""
    parts = [p.strip() for p in text.replace("،", ",").split(",")]
    return [p for p in parts if p.isdigit() and len(p) == 10]


def durationText(phone: str) -> str:
    return (
        f"{b('Step 1 of 2')}  {c(phone)}\n\n"
        f"Select test duration."
    )


def workersText(phone: str, duration: int) -> str:
    return (
        f"{b('Step 2 of 2')}  {c(phone)}\n\n"
        f"Duration   {c(formatDuration(duration))}\n\n"
        f"Select number of workers."
    )


def buildConfirmText(data: dict, proxyInfo: str = "") -> str:
    proxyLabel = proxyInfo if proxyInfo else ("Proxy" if data.get("useProxy") else "Direct")
    phones     = data.get("phones", [data.get("phone", "")])
    phoneStr   = ", ".join(phones) if len(phones) > 1 else phones[0] if phones else data.get("phone", "")
    nukeStr    = f"\n{b('NUKE MODE')}  {c('MAX DESTRUCTION')}" if data.get("nukeMode") else ""
    return (
        f"{b('Ready to Launch')}{nukeStr}\n\n"
        f"Phone{'s' if len(phones) > 1 else ''}   {c(hEsc(phoneStr))}\n"
        f"Targets    {c(str(len(phones)))}\n"
        f"Duration   {c(formatDuration(data['duration']))}\n"
        f"Workers    {c(str(data['workers']))}\n"
        f"Proxy      {c(proxyLabel)}\n\n"
        f"{i('Tap Launch to start or Edit to go back.')}"
    )


def buildDashboardText(snap: dict, phones: list, duration: int) -> str:
    elapsed    = snap["elapsed"]
    lines      = []
    rl_count   = 0
    dead_count = 0

    for name, s in snap["perApi"].items():
        if s.get("status") == "ratelimited":
            rl_count += 1
        elif s.get("status") == "dead":
            dead_count += 1
        if s.get("requests", 0) > 0:
            conf       = s.get("confirmed", 0)
            req        = s["requests"]
            ms         = s["avgMs"]
            status_tag = " [RL]" if s.get("status") == "ratelimited" else (" [D]" if s.get("status") == "dead" else "")
            lines.append(
                f"<code>{hEsc(name[:14]):<14}</code>{status_tag}  "
                f"{c(str(conf))} otp  {req}req  {ms}ms"
            )

    apiBlock = "\n".join(lines[:10]) if lines else i("Sending requests...")
    if len(lines) > 10:
        apiBlock += f"\n{i(f'+ {len(lines)-10} more')}"

    bar_total  = 20
    pct        = min(elapsed / duration, 1.0) if duration > 0 else 0
    bar_filled = int(bar_total * pct)
    bar        = "█" * bar_filled + "░" * (bar_total - bar_filled)

    totalReqs  = snap.get("totalReqs", 0)
    confirmed  = snap.get("confirmed", 0)
    responses  = snap.get("responses", 0)
    remaining  = max(0, duration - int(elapsed))
    rps_str    = str(snap["rps"])
    surges     = snap.get("surgeCount", 0)
    honeypots  = sum(1 for s in snap["perApi"].values() if s.get("status") == "honeypot")

    status_bits = []
    if rl_count:   status_bits.append(f"RL {rl_count}")
    if dead_count: status_bits.append(f"Dead {dead_count}")
    if honeypots:  status_bits.append(f"Fake {honeypots}")
    status_str = "  [ " + "  ".join(status_bits) + " ]" if status_bits else ""

    phoneDisplay = ", ".join(phones) if len(phones) <= 3 else f"{phones[0]} +{len(phones)-1} more"
    surgeStr = f"  {i(str(surges) + ' surges')}" if surges > 0 else ""

    return (
        f"{b('Test Running')}  {c(hEsc(phoneDisplay))}\n"
        f"<code>{bar}</code>  {c(f'{int(pct*100)}%')}  {i(f'{int(elapsed)}s / {duration}s')}\n\n"
        f"Remaining   {c(formatDuration(remaining))}\n"
        f"Requests    {c(str(totalReqs))}  {i(rps_str + ' r/s')}{surgeStr}\n"
        f"Confirmed   {c(str(confirmed))}\n"
        f"2xx Total   {c(str(responses))}\n"
        f"Errors      {c(str(snap['errors']))}{status_str}\n\n"
        f"{b('APIs')}\n{apiBlock}"
    )


def buildSummaryText(snap: dict, phones: list) -> str:
    sortedApis = sorted(
        snap["perApi"].items(),
        key=lambda x: (x[1].get("confirmed", 0), x[1].get("responses", 0)),
        reverse=True,
    )
    topLines = []
    for name, s in sortedApis[:6]:
        if s.get("requests", 0) > 0:
            topLines.append(
                f"<code>{hEsc(name[:16]):<16}</code>  "
                f"{c(str(s.get('confirmed',0)))} otp  "
                f"{s.get('responses',0)} ok  "
                f"{s['requests']} req"
            )

    topBlock      = "\n".join(topLines) if topLines else i("No responses recorded.")
    elapsed       = int(snap["elapsed"])
    totalReqs     = snap.get("totalReqs", 0)
    confirmed     = snap.get("confirmed", 0)
    phoneDisplay  = ", ".join(phones) if len(phones) <= 3 else f"{phones[0]} +{len(phones)-1} more"

    return (
        f"{b('Test Complete')}\n"
        f"{c(hEsc(phoneDisplay))}\n\n"
        f"Duration    {c(formatDuration(elapsed))}\n"
        f"Requests    {c(str(totalReqs))}\n"
        f"Confirmed   {c(str(confirmed))}\n"
        f"2xx Total   {c(str(snap['responses']))}\n"
        f"Errors      {c(str(snap['errors']))}\n"
        f"Req/sec     {c(str(snap['rps']))}\n\n"
        f"{b('Top APIs')}\n{topBlock}"
    )


async def dashboardLoop(runner, message, phones, duration, userId, state):
    lastText = ""
    while runner.isRunning:
        await asyncio.sleep(DASHBOARD_UPDATE_INTERVAL)
        snap    = runner.stats.snapshot()
        newText = buildDashboardText(snap, phones, duration)
        if newText != lastText:
            try:
                await message.edit_text(newText, reply_markup=runningKeyboard(), parse_mode=PM)
                lastText = newText
            except Exception:
                pass

    if not summaryShown.get(userId, False):
        summaryShown[userId] = True
        snap     = runner.stats.snapshot()
        rid      = activeRecordIds.get(userId)
        _saveHistory(userId, snap, rid)
        _lastConfig[userId] = {
            "phone":    runner.phones[0],
            "phones":   runner.phones,
            "duration": runner.duration,
            "workers":  runner.workers,
        }
        try:
            await message.edit_text(
                buildSummaryText(snap, phones),
                reply_markup=finishedKeyboard(), parse_mode=PM
            )
        except Exception:
            await message.answer(
                buildSummaryText(snap, phones),
                reply_markup=finishedKeyboard(), parse_mode=PM
            )

    activeRunners.pop(userId, None)
    dashboardTasks.pop(userId, None)
    summaryShown.pop(userId, None)
    activeRecordIds.pop(userId, None)
    await state.clear()


def _saveHistory(userId, snap, recordId=None):
    if recordId is None:
        recordId = activeRecordIds.get(userId)
    if recordId:
        try:
            apiSnapshot = json.dumps({
                name: {
                    "confirmed": s.get("confirmed", 0),
                    "requests":  s.get("requests", 0),
                    "responses": s.get("responses", 0),
                    "errors":    s.get("errors", 0),
                }
                for name, s in snap.get("perApi", {}).items()
                if s.get("requests", 0) > 0
            })
            totalReqs = snap.get("totalReqs", 0)
            otpHits   = snap.get("confirmed", 0)
            db.finishTestRecord(
                recordId=recordId,
                totalReqs=totalReqs,
                otpHits=otpHits,
                errors=snap["errors"],
                rps=snap["rps"],
                apiSnapshot=apiSnapshot,
            )
            db.updateUserStats(userId, totalReqs, otpHits)
            for name, s in snap.get("perApi", {}).items():
                if s.get("requests", 0) > 0:
                    db.recordApiUsage(
                        name=name,
                        reqs=s.get("requests", 0),
                        otps=s.get("confirmed", 0),
                        errors=s.get("errors", 0),
                    )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry: Start Test
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:start_test")
async def cbStartTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("A test is already running. Stop it first.", show_alert=True)
        return
    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        u = db.getUser(userId)
        if u and u["isBanned"]:
            await callback.answer("Your account has been restricted.", show_alert=True)
            return
        await callback.answer(f"Daily limit reached. {testsToday}/{dailyLimit} used.", show_alert=True)
        db.recordLimitHit(userId)
        try:
            u        = db.getUser(userId)
            username = f"@{u['username']}" if u and u.get("username") else str(userId)
            await callback.bot.send_message(
                ADMIN_ID,
                f"{b('Limit Hit')}\n\n{hEsc(username)} ({userId}) hit daily limit of {dailyLimit}.",
                parse_mode=PM
            )
        except Exception:
            pass
        return
    await state.clear()
    await state.set_state(TestWizard.phone)

    favs    = db.getFavorites(userId)
    builder = InlineKeyboardBuilder()
    if favs:
        for f in favs:
            label = f["label"] or f["phone"]
            builder.button(text=label, callback_data=f"startfav:{f['phone']}")
    builder.button(text="Back", callback_data="nav:main_menu")
    builder.adjust(*([1] * len(favs)), 1)

    text = (
        f"{b('Start Test')}\n\n"
        f"Enter target number(s).\n"
        f"{i('Single: 9876543210')}\n"
        f"{i('Multi: 9876543210, 9876543211, 9876543212')}"
    )
    if favs:
        text += f"\n\n{i('Or pick a saved favorite:')}"

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode=PM)
    await callback.answer()


@router.callback_query(F.data.startswith("startfav:"), StateFilter(TestWizard.phone))
async def cbStartFromFavorite(callback: CallbackQuery, state: FSMContext) -> None:
    phone  = callback.data.split(":", 1)[1]
    userId = callback.from_user.id
    if phone == PROTECTED_NUMBER and userId != ADMIN_ID:
        await callback.answer(random.choice(PROTECTED_RESPONSES), show_alert=True)
        return
    if db.isPhoneBlacklisted(phone) and userId != ADMIN_ID:
        await callback.answer("That number is not available for testing.", show_alert=True)
        return
    await state.update_data(phone=phone, phones=[phone])
    await state.set_state(TestWizard.duration)
    await callback.message.edit_text(
        durationText(phone), reply_markup=durationKeyboard(), parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Phone input
# ---------------------------------------------------------------------------

@router.message(StateFilter(TestWizard.phone))
async def handlePhone(message: Message, state: FSMContext) -> None:
    userId = message.from_user.id
    phones = parsePhones(message.text or "")

    if not phones:
        await message.answer(
            f"{b('Invalid')}\n\n"
            f"Enter one or more 10-digit numbers.\n"
            f"{i('Single: 9876543210')}\n"
            f"{i('Multi: 9876543210, 9876543211')}",
            parse_mode=PM
        )
        return

    # Filter protected/blacklisted
    if userId != ADMIN_ID:
        if PROTECTED_NUMBER in phones:
            await message.answer(random.choice(PROTECTED_RESPONSES))
            return
        phones = [p for p in phones if not db.isPhoneBlacklisted(p)]
        if not phones:
            await message.answer("All entered numbers are blacklisted.")
            return

    # Cap at 5 numbers for non-admin
    if userId != ADMIN_ID and len(phones) > 5:
        phones = phones[:5]

    primaryPhone = phones[0]
    await state.update_data(phone=primaryPhone, phones=phones)
    await state.set_state(TestWizard.duration)

    phoneStr = ", ".join(phones) if len(phones) > 1 else primaryPhone
    infoStr  = f" {i(f'({len(phones)} targets)')}" if len(phones) > 1 else ""
    await message.answer(
        f"{b('Step 1 of 2')}  {c(hEsc(phoneStr))}{infoStr}\n\nSelect test duration.",
        reply_markup=durationKeyboard(), parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Step 1: Duration
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("dur:"), StateFilter(TestWizard.duration))
async def cbDuration(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":")[1]
    if value == "custom":
        await state.set_state(TestWizard.durationCustom)
        builder = InlineKeyboardBuilder()
        builder.button(text="Back", callback_data="nav:duration")
        await callback.message.edit_text(
            f"{b('Custom Duration')}\n\nEnter a value: {c('30s')}  {c('5m')}  {c('1h')}\n{i('Min: 5s  Max: 24h')}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return
    data = await state.get_data()
    await state.update_data(duration=int(value))
    await state.set_state(TestWizard.workers)
    await callback.message.edit_text(
        workersText(data.get("phone", ""), int(value)),
        reply_markup=workersKeyboard(), parse_mode=PM
    )
    await callback.answer(f"Duration: {formatDuration(int(value))}")


@router.message(StateFilter(TestWizard.durationCustom))
async def handleDurationCustom(message: Message, state: FSMContext) -> None:
    seconds = parseTime(message.text or "")
    if seconds is None or seconds < 5 or seconds > 86400:
        await message.answer(
            f"{b('Invalid')}  Try: {c('30s')} {c('5m')} {c('2h')}  {i('min 5s, max 24h')}",
            parse_mode=PM
        )
        return
    data = await state.get_data()
    await state.update_data(duration=seconds)
    await state.set_state(TestWizard.workers)
    await message.answer(
        workersText(data.get("phone", ""), seconds),
        reply_markup=workersKeyboard(), parse_mode=PM
    )


@router.callback_query(F.data == "nav:duration")
async def cbBackToDuration(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.duration)
    data = await state.get_data()
    await callback.message.edit_text(
        durationText(data.get("phone", "")),
        reply_markup=durationKeyboard(), parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Step 2: Workers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("wrk:"), StateFilter(TestWizard.workers))
async def cbWorkers(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":")[1]
    if value == "custom":
        await state.set_state(TestWizard.workersCustom)
        builder = InlineKeyboardBuilder()
        builder.button(text="Back", callback_data="nav:workers")
        await callback.message.edit_text(
            f"{b('Custom Workers')}\n\nEnter a number between {c('1')} and {c('64')}.",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return
    await state.update_data(workers=int(value))
    await state.set_state(TestWizard.proxy)
    hasProxies = proxyManager.hasProxies()
    await callback.message.edit_text(
        f"{b('Proxy Settings')}\n\nUse a proxy for this test?",
        reply_markup=proxyKeyboard(hasProxies), parse_mode=PM
    )
    await callback.answer(f"Workers: {value}")


@router.message(StateFilter(TestWizard.workersCustom))
async def handleWorkersCustom(message: Message, state: FSMContext) -> None:
    try:
        workers = int((message.text or "").strip())
        if not 1 <= workers <= 64:
            raise ValueError
    except ValueError:
        await message.answer(f"Enter a number between {c('1')} and {c('64')}.", parse_mode=PM)
        return
    await state.update_data(workers=workers)
    await state.set_state(TestWizard.proxy)
    hasProxies = proxyManager.hasProxies()
    await message.answer(
        f"{b('Proxy Settings')}\n\nUse a proxy for this test?",
        reply_markup=proxyKeyboard(hasProxies), parse_mode=PM
    )


@router.callback_query(F.data == "nav:workers")
async def cbBackToWorkers(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.workers)
    data = await state.get_data()
    await callback.message.edit_text(
        workersText(data.get("phone", ""), data.get("duration", 0)),
        reply_markup=workersKeyboard(), parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("proxy:"), StateFilter(TestWizard.proxy))
async def cbProxy(callback: CallbackQuery, state: FSMContext) -> None:
    useProxy = callback.data == "proxy:file"
    await state.update_data(useProxy=useProxy)
    if useProxy:
        await state.set_state(TestWizard.proxyChecking)
        statusMsg = await callback.message.edit_text(
            f"{b('Checking Proxies')}\n\n{i('Verifying proxy pool...')}", parse_mode=PM
        )
        await callback.answer()
        allProxies = proxyManager.getAllProxies()
        if not allProxies:
            await state.update_data(useProxy=False, workingProxies=[])
            data = await state.get_data()
            await state.set_state(TestWizard.confirm)
            await statusMsg.edit_text(
                buildConfirmText(data, "None (no proxies loaded)"),
                reply_markup=confirmKeyboard(), parse_mode=PM
            )
            return
        working   = await validateProxies(allProxies)
        dead      = len(allProxies) - len(working)
        await state.update_data(workingProxies=working)
        proxyInfo = f"{len(working)} working / {dead} dead" if working else "None (0 working)"
        if not working:
            await state.update_data(useProxy=False)
        data = await state.get_data()
        await state.set_state(TestWizard.confirm)
        await statusMsg.edit_text(
            buildConfirmText(data, proxyInfo),
            reply_markup=confirmKeyboard(), parse_mode=PM
        )
    else:
        data = await state.get_data()
        await state.set_state(TestWizard.confirm)
        await callback.message.edit_text(
            buildConfirmText(data), reply_markup=confirmKeyboard(), parse_mode=PM
        )
        await callback.answer()


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "confirm:edit", StateFilter(TestWizard.confirm))
async def cbConfirmEdit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.workers)
    data = await state.get_data()
    await callback.message.edit_text(
        workersText(data.get("phone", ""), data.get("duration", 0)),
        reply_markup=workersKeyboard(), parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:cancel", StateFilter(TestWizard.confirm))
async def cbCancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        i("Test cancelled."), reply_markup=mainMenuKeyboard(), parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:start", StateFilter(TestWizard.confirm))
async def cbConfirmStart(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("Already running.", show_alert=True)
        return
    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        await state.clear()
        await callback.message.edit_text(
            f"{b('Daily limit reached')}\n\n{c(f'{testsToday}/{dailyLimit}')} used today.\n{i('Resets at midnight IST.')}",
            reply_markup=mainMenuKeyboard(), parse_mode=PM
        )
        await callback.answer()
        return

    data           = await state.get_data()
    await state.set_state(TestWizard.running)
    phones         = data.get("phones", [data.get("phone", "")])
    phone          = phones[0]
    duration       = data["duration"]
    workers        = data["workers"]
    useProxy       = data.get("useProxy", False)
    workingProxies = data.get("workingProxies", [])
    nukeMode       = data.get("nukeMode", False)

    db.incrementTestCount(userId)
    recordId = db.startTestRecord(userId, phone, duration, workers)
    activeRecordIds[userId] = recordId

    runner = TesterRunner(
        phone=",".join(phones),
        duration=duration,
        workers=workers,
        useProxy=useProxy,
        proxyList=workingProxies,
        userId=userId,
        bot=callback.bot,
        nukeMode=nukeMode,
    )
    activeRunners[userId] = runner
    summaryShown[userId]  = False

    _, testsNow, limitNow = db.canRunTest(userId)
    nukeStr = f"  {b('NUKE MODE')}" if nukeMode else ""
    phoneDisplay = ", ".join(phones) if len(phones) <= 3 else f"{phone} +{len(phones)-1} more"
    dashMsg = await callback.message.edit_text(
        f"{b('Test Running')}{nukeStr}  {c(hEsc(phoneDisplay))}\n\n"
        f"{i('Initializing burst...')}\n\n"
        f"{c(f'Tests today: {testsNow}/{limitNow}')}",
        reply_markup=runningKeyboard(), parse_mode=PM
    )
    await callback.answer()
    await runner.start()
    task = asyncio.create_task(
        dashboardLoop(runner, dashMsg, phones, duration, userId, state)
    )
    dashboardTasks[userId] = task


# ---------------------------------------------------------------------------
# Nuke mode launch (admin only, from callback)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("nuke:launch:"))
async def cbNukeLaunch(callback: CallbackQuery, state: FSMContext) -> None:
    from bot.config import ADMIN_ID
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Access denied.", show_alert=True)
        return
    phone  = callback.data.split(":", 2)[2]
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("Already running. Stop it first.", show_alert=True)
        return

    await state.set_state(TestWizard.running)
    duration = 300  # 5 min nuke
    workers  = 64

    db.incrementTestCount(userId)
    recordId = db.startTestRecord(userId, phone, duration, workers)
    activeRecordIds[userId] = recordId

    runner = TesterRunner(
        phone=phone, duration=duration, workers=workers,
        useProxy=False, userId=userId, bot=callback.bot,
        nukeMode=True,
    )
    activeRunners[userId] = runner
    summaryShown[userId]  = False

    dashMsg = await callback.message.edit_text(
        f"{b('NUKE MODE ACTIVE')}\n\n"
        f"Target  {c(phone)}\n"
        f"Duration  {c('5 min')}\n"
        f"Workers   {c('64 per API')}\n"
        f"Burst     {c('PERMANENT')}\n\n"
        f"{i('Maximum destruction engaged.')}",
        reply_markup=runningKeyboard(), parse_mode=PM
    )
    await callback.answer("NUKE LAUNCHED")
    await runner.start()
    task = asyncio.create_task(
        dashboardLoop(runner, dashMsg, [phone], duration, userId, state)
    )
    dashboardTasks[userId] = task


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "test:stop")
async def cbStopTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    runner = activeRunners.get(userId)
    if not runner:
        await callback.answer("No active test.", show_alert=True)
        return
    await callback.answer("Stopping...")
    summaryShown[userId] = True
    task = dashboardTasks.pop(userId, None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await runner.stop()
    snap = runner.stats.snapshot()
    rid  = activeRecordIds.get(userId)
    _saveHistory(userId, snap, rid)
    _lastConfig[userId] = {
        "phone":    runner.phones[0],
        "phones":   runner.phones,
        "duration": runner.duration,
        "workers":  runner.workers,
    }
    activeRunners.pop(userId, None)
    summaryShown.pop(userId, None)
    activeRecordIds.pop(userId, None)
    await state.clear()
    summary = buildSummaryText(snap, runner.phones)
    try:
        await callback.message.edit_text(summary, reply_markup=finishedKeyboard(), parse_mode=PM)
    except Exception:
        await callback.message.answer(summary, reply_markup=finishedKeyboard(), parse_mode=PM)


# ---------------------------------------------------------------------------
# Repeat
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "test:repeat")
async def cbRepeatTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("A test is already running.", show_alert=True)
        return
    last = _lastConfig.get(userId)
    if not last:
        await callback.answer("No previous test to repeat.", show_alert=True)
        return
    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        await callback.answer(f"Daily limit reached. {testsToday}/{dailyLimit} used.", show_alert=True)
        return

    phones   = last.get("phones", [last["phone"]])
    duration = last["duration"]
    workers  = last["workers"]

    if userId != ADMIN_ID:
        phones = [p for p in phones if not db.isPhoneBlacklisted(p)]
    if not phones:
        await callback.answer("Number(s) are now blacklisted.", show_alert=True)
        return

    await state.set_state(TestWizard.running)
    db.incrementTestCount(userId)
    recordId = db.startTestRecord(userId, phones[0], duration, workers)
    activeRecordIds[userId] = recordId

    runner = TesterRunner(
        phone=",".join(phones), duration=duration, workers=workers,
        useProxy=False, userId=userId, bot=callback.bot,
    )
    activeRunners[userId] = runner
    summaryShown[userId]  = False

    phoneDisplay = ", ".join(phones) if len(phones) <= 3 else f"{phones[0]} +{len(phones)-1} more"
    _, testsNow, limitNow = db.canRunTest(userId)
    dashMsg = await callback.message.edit_text(
        f"{b('Test Running')}  {c(hEsc(phoneDisplay))}\n\n"
        f"{i('Repeating last test...')}\n\n"
        f"{c(f'Tests today: {testsNow}/{limitNow}')}",
        reply_markup=runningKeyboard(), parse_mode=PM
    )
    await callback.answer("Repeating...")
    await runner.start()
    task = asyncio.create_task(
        dashboardLoop(runner, dashMsg, phones, duration, userId, state)
    )
    dashboardTasks[userId] = task


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:history")
async def cbUserHistory(callback: CallbackQuery) -> None:
    userId  = callback.from_user.id
    history = db.getUserHistory(userId, limit=10)
    if not history:
        builder = InlineKeyboardBuilder()
        builder.button(text="Main Menu", callback_data="nav:main_menu")
        await callback.message.edit_text(
            f"{b('My History')}\n\n{i('No tests run yet.')}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    lines   = [f"{b('My History')}  {c(f'last {len(history)}')}\n"]
    for h in history:
        dt = datetime.fromtimestamp(h["startedAt"], tz=IST).strftime("%d %b %H:%M")
        lines.append(
            f"{c(dt)}  {h['phone']}  {h['duration']}s  "
            f"OTP {h['otpHits']}  REQ {h['totalReqs']}"
        )
        builder.button(
            text=f"{h['phone']}  {h['duration']}s  OTP {h['otpHits']}",
            callback_data=f"hist:detail:{h['id']}"
        )
    builder.button(text="Main Menu", callback_data="nav:main_menu")
    builder.adjust(1)
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("hist:detail:"))
async def cbHistDetail(callback: CallbackQuery) -> None:
    recordId = int(callback.data.split(":")[2])
    h = db.getTestRecord(recordId)
    if not h or h["userId"] != callback.from_user.id:
        await callback.answer("Not found.", show_alert=True)
        return

    dt    = datetime.fromtimestamp(h["startedAt"], tz=IST).strftime("%d %b %Y %H:%M")
    lines = [
        f"{b('Test Detail')}\n",
        f"Phone      {c(h['phone'])}",
        f"Date       {c(dt)}",
        f"Duration   {c(str(h['duration']) + 's')}",
        f"Workers    {c(str(h['workers']))}",
        f"Requests   {c(str(h['totalReqs']))}",
        f"OTPs       {c(str(h['otpHits']))}",
        f"Errors     {c(str(h['errors']))}",
        f"Req/sec    {c(str(h['rps']))}",
    ]

    snap = h.get("apiSnapshot")
    if snap:
        try:
            data    = json.loads(snap)
            sorted_ = sorted(data.items(), key=lambda x: x[1].get("confirmed", 0), reverse=True)
            top     = [(n, s) for n, s in sorted_ if s.get("requests", 0) > 0][:6]
            if top:
                lines.append(f"\n{b('API Breakdown')}")
                for name, s in top:
                    lines.append(
                        f"<code>{hEsc(name[:16]):<16}</code>  "
                        f"{c(str(s.get('confirmed', 0)))} otp  "
                        f"{s.get('requests', 0)} req"
                    )
        except Exception:
            pass

    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="menu:history")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()
