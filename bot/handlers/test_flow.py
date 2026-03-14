from __future__ import annotations

import asyncio
from typing import Dict, Optional

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery

from bot.keyboards.menus import (
    durationKeyboard, workersKeyboard, proxyKeyboard,
    confirmKeyboard, runningKeyboard, finishedKeyboard, mainMenuKeyboard,
)
from bot.services.tester_runner import TesterRunner, validateProxies
from bot.services.proxy_manager import proxyManager
from bot.services.database import db
from bot.config import DASHBOARD_UPDATE_INTERVAL, ADMIN_ID, PROTECTED_NUMBER
from bot.utils import PM, b, i, c, hEsc

import random

router = Router()

activeRunners: Dict[int, TesterRunner] = {}
dashboardTasks: Dict[int, asyncio.Task] = {}
summaryShown: Dict[int, bool] = {}
activeRecordIds: Dict[int, int] = {}

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
    try:
        if t.endswith("s"): return int(t[:-1])
        if t.endswith("m"): return int(t[:-1]) * 60
        if t.endswith("h"): return int(t[:-1]) * 3600
        return int(t)
    except (ValueError, AttributeError):
        return None


def buildConfirmText(data: dict, proxyInfo: str = "") -> str:
    proxyLabel = proxyInfo if proxyInfo else ("Proxy file" if data.get("useProxy") else "None")
    return (
        f"{b('Confirm Test')}\n\n"
        f"Phone     {c(data['phone'])}\n"
        f"Duration  {c(formatDuration(data['duration']))}\n"
        f"Workers   {c(str(data['workers']))}\n"
        f"Proxy     {c(proxyLabel)}\n\n"
        f"{i('Ready to launch.')}"
    )


def buildDashboardText(snap: dict, phone: str, duration: int) -> str:
    elapsed = snap["elapsed"]

    lines      = []
    rl_count   = 0
    dead_count = 0
    for name, s in snap["perApi"].items():
        if s.get("status") == "ratelimited":
            rl_count += 1
        elif s.get("status") == "dead":
            dead_count += 1
        elif s.get("requests", 0) > 0:
            lines.append(
                f"<code>{hEsc(name[:18]):<18}</code>  "
                f"{s['requests']}req  {s.get('confirmed',0)}otp  {s['avgMs']}ms"
            )

    if not lines:
        apiBlock = i("Waiting for responses...")
    else:
        apiBlock = "\n".join(lines[:12])
        if len(lines) > 12:
            apiBlock += f"\n{i(f'+{len(lines)-12} more')}"

    bar_total  = 18
    bar_filled = int(bar_total * min(elapsed / duration, 1)) if duration > 0 else 0
    bar        = "█" * bar_filled + "░" * (bar_total - bar_filled)

    status_bits = []
    if rl_count:   status_bits.append(f"RL {c(str(rl_count))}")
    if dead_count: status_bits.append(f"Dead {c(str(dead_count))}")
    status_line = "  ·  " + "  ".join(status_bits) if status_bits else ""

    totalReqs = snap.get("totalReqs", snap.get("total", 0))
    confirmed = snap.get("confirmed", snap.get("otpSent", 0))
    responses = snap.get("responses", 0)

    return (
        f"{b('Test Running')}  {c(phone)}\n"
        f"<code>{bar}</code>  {c(f'{int(elapsed)}s / {duration}s')}\n\n"
        f"Requests   {c(str(totalReqs))}  ·  RPS {c(str(snap['rps']))}{status_line}\n"
        f"Confirmed  {c(str(confirmed))}  ·  2xx {c(str(responses))}\n"
        f"Errors     {c(str(snap['errors']))}\n\n"
        f"{b('APIs')}\n{apiBlock}"
    )


def buildSummaryText(snap: dict, phone: str) -> str:
    sortedApis = sorted(
        snap["perApi"].items(),
        key=lambda x: (x[1].get("confirmed", 0), x[1].get("responses", 0)),
        reverse=True,
    )
    topLines = []
    for name, s in sortedApis[:6]:
        if s.get("requests", 0) > 0:
            topLines.append(
                f"<code>{hEsc(name[:18]):<18}</code>  "
                f"{s.get('confirmed',0)}otp  {s.get('responses',0)}ok  {s['requests']}req"
            )

    topBlock  = "\n".join(topLines) if topLines else i("No successful responses.")
    elapsed   = int(snap["elapsed"])
    totalReqs = snap.get("totalReqs", snap.get("total", 0))
    confirmed = snap.get("confirmed", snap.get("otpSent", 0))
    responses = snap.get("responses", 0)

    return (
        f"{b('Test Complete')}  {c(phone)}\n\n"
        f"Duration   {c(formatDuration(elapsed))}\n"
        f"Requests   {c(str(totalReqs))}\n"
        f"Confirmed  {c(str(confirmed))}\n"
        f"2xx Total  {c(str(responses))}\n"
        f"Errors     {c(str(snap['errors']))}\n"
        f"Req/sec    {c(str(snap['rps']))}\n\n"
        f"{b('Top APIs')}\n{topBlock}"
    )


async def dashboardLoop(
    runner: TesterRunner,
    message: Message,
    phone: str,
    duration: int,
    userId: int,
    state: FSMContext,
) -> None:
    lastText = ""
    while runner.isRunning:
        await asyncio.sleep(DASHBOARD_UPDATE_INTERVAL)
        snap    = runner.stats.snapshot()
        newText = buildDashboardText(snap, phone, duration)
        if newText != lastText:
            try:
                await message.edit_text(newText, reply_markup=runningKeyboard(), parse_mode=PM)
                lastText = newText
            except Exception:
                pass

    if not summaryShown.get(userId, False):
        summaryShown[userId] = True
        snap    = runner.stats.snapshot()
        _saveHistory(userId, snap)
        summary = buildSummaryText(snap, phone)
        try:
            await message.edit_text(summary, reply_markup=finishedKeyboard(), parse_mode=PM)
        except Exception:
            await message.answer(summary, reply_markup=finishedKeyboard(), parse_mode=PM)

    activeRunners.pop(userId, None)
    dashboardTasks.pop(userId, None)
    summaryShown.pop(userId, None)
    activeRecordIds.pop(userId, None)
    await state.clear()


def _saveHistory(userId: int, snap: dict) -> None:
    recordId = activeRecordIds.get(userId)
    if recordId:
        try:
            db.finishTestRecord(
                recordId=recordId,
                totalReqs=snap.get("totalReqs", snap.get("total", 0)),
                otpHits=snap.get("confirmed", snap.get("otpSent", 0)),
                errors=snap["errors"],
                rps=snap["rps"],
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Handlers
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
        await callback.answer(
            f"Daily limit reached. {testsToday}/{dailyLimit} tests used today.",
            show_alert=True
        )
        try:
            u        = db.getUser(userId)
            username = f"@{u['username']}" if u and u.get("username") else str(userId)
            await callback.bot.send_message(
                ADMIN_ID,
                f"{b('Limit Hit')}\n\n{hEsc(username)} ({userId}) hit their daily limit of {dailyLimit}.",
                parse_mode=PM
            )
        except Exception:
            pass
        return

    await state.clear()
    await state.set_state(TestWizard.phone)
    await callback.message.edit_text(
        f"{b('Start Test')}\n\nEnter the 10-digit mobile number.\n{i('Example: 9876543210')}",
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(TestWizard.phone))
async def handlePhone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()

    if not phone.isdigit() or len(phone) != 10:
        await message.answer(
            f"{b('Invalid number')}\n\nEnter exactly 10 digits, no spaces.\n{i('Example: 9876543210')}",
            parse_mode=PM
        )
        return

    if phone == PROTECTED_NUMBER and message.from_user.id != ADMIN_ID:
        await message.answer(random.choice(PROTECTED_RESPONSES))
        return

    if db.isPhoneBlacklisted(phone) and message.from_user.id != ADMIN_ID:
        await message.answer("That number is not available for testing.")
        return

    await state.update_data(phone=phone)
    await state.set_state(TestWizard.duration)
    await message.answer(
        f"{b('Test Duration')}\n\nHow long should the test run?",
        reply_markup=durationKeyboard(),
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("duration:"), StateFilter(TestWizard.duration))
async def cbDuration(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":")[1]
    if value == "custom":
        await state.set_state(TestWizard.durationCustom)
        await callback.message.edit_text(
            f"{b('Custom Duration')}\n\n"
            f"Enter a value with a unit: {c('30s')}  {c('5m')}  {c('1h')}\n\n"
            f"{i('Min: 5s  ·  Max: 24h')}",
            reply_markup=backToDurationKeyboard(),
            parse_mode=PM
        )
        await callback.answer()
        return
    await state.update_data(duration=int(value))
    await state.set_state(TestWizard.workers)
    await callback.message.edit_text(
        f"{b('Sender Workers')}\n\nMore workers = more concurrent requests.",
        reply_markup=workersKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(TestWizard.durationCustom))
async def handleDurationCustom(message: Message, state: FSMContext) -> None:
    raw     = (message.text or "").strip()
    seconds = parseTime(raw)
    if seconds is None or seconds < 5 or seconds > 86400:
        await message.answer(
            f"{b('Invalid duration')}\n\nExamples: {c('30s')}  {c('5m')}  {c('2h')}\n{i('Min: 5s  Max: 24h')}",
            parse_mode=PM
        )
        return
    await state.update_data(duration=seconds)
    await state.set_state(TestWizard.workers)
    await message.answer(
        f"{b('Sender Workers')}\n\nMore workers = more concurrent requests.",
        reply_markup=workersKeyboard(),
        parse_mode=PM
    )


@router.callback_query(F.data == "nav:duration")
async def cbBackToDuration(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.duration)
    await callback.message.edit_text(
        f"{b('Test Duration')}\n\nHow long should the test run?",
        reply_markup=durationKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("workers:"), StateFilter(TestWizard.workers))
async def cbWorkers(callback: CallbackQuery, state: FSMContext) -> None:
    value = callback.data.split(":")[1]
    if value == "custom":
        await state.set_state(TestWizard.workersCustom)
        await callback.message.edit_text(
            f"{b('Custom Workers')}\n\nEnter a number between {c('1')} and {c('64')}.",
            reply_markup=backToWorkersKeyboard(),
            parse_mode=PM
        )
        await callback.answer()
        return
    await state.update_data(workers=int(value))
    await state.set_state(TestWizard.proxy)
    hasProxies = proxyManager.hasProxies()
    await callback.message.edit_text(
        f"{b('Proxy Settings')}\n\nUse a proxy for this test?",
        reply_markup=proxyKeyboard(hasProxies),
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(TestWizard.workersCustom))
async def handleWorkersCustom(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    try:
        workers = int(raw)
        if not 1 <= workers <= 64:
            raise ValueError
    except ValueError:
        await message.answer(
            f"Enter a whole number between {c('1')} and {c('64')}.",
            parse_mode=PM
        )
        return
    await state.update_data(workers=workers)
    await state.set_state(TestWizard.proxy)
    hasProxies = proxyManager.hasProxies()
    await message.answer(
        f"{b('Proxy Settings')}\n\nUse a proxy for this test?",
        reply_markup=proxyKeyboard(hasProxies),
        parse_mode=PM
    )


@router.callback_query(F.data == "nav:workers")
async def cbBackToWorkers(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TestWizard.workers)
    await callback.message.edit_text(
        f"{b('Sender Workers')}\n\nMore workers = more concurrent requests.",
        reply_markup=workersKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("proxy:"), StateFilter(TestWizard.proxy))
async def cbProxy(callback: CallbackQuery, state: FSMContext) -> None:
    useProxy = callback.data == "proxy:file"
    await state.update_data(useProxy=useProxy)

    if useProxy:
        await state.set_state(TestWizard.proxyChecking)
        statusMsg = await callback.message.edit_text(
            f"{b('Checking Proxies')}\n\n{i('Verifying proxy list, please wait...')}",
            parse_mode=PM
        )
        await callback.answer()

        allProxies = proxyManager.getAllProxies()
        if not allProxies:
            await state.update_data(useProxy=False, workingProxies=[])
            data = await state.get_data()
            await state.set_state(TestWizard.confirm)
            await statusMsg.edit_text(
                buildConfirmText(data, proxyInfo="None (no proxies loaded)"),
                reply_markup=confirmKeyboard(),
                parse_mode=PM
            )
            return

        working  = await validateProxies(allProxies)
        dead     = len(allProxies) - len(working)
        await state.update_data(workingProxies=working)
        proxyInfo = f"{len(working)} working / {dead} dead"
        if not working:
            await state.update_data(useProxy=False)
            proxyInfo = "None (0 working proxies)"

        data = await state.get_data()
        await state.set_state(TestWizard.confirm)
        await statusMsg.edit_text(
            buildConfirmText(data, proxyInfo=proxyInfo),
            reply_markup=confirmKeyboard(),
            parse_mode=PM
        )
    else:
        await state.set_state(TestWizard.confirm)
        data = await state.get_data()
        await callback.message.edit_text(
            buildConfirmText(data),
            reply_markup=confirmKeyboard(),
            parse_mode=PM
        )
        await callback.answer()


@router.callback_query(F.data == "confirm:cancel", StateFilter(TestWizard.confirm))
async def cbCancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        i("Test cancelled."),
        reply_markup=mainMenuKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "confirm:start", StateFilter(TestWizard.confirm))
async def cbConfirmStart(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    if userId in activeRunners:
        await callback.answer("A test is already running.", show_alert=True)
        return

    allowed, testsToday, dailyLimit = db.canRunTest(userId)
    if not allowed:
        await state.clear()
        await callback.message.edit_text(
            f"{b('Daily limit reached')}\n\n{c(f'{testsToday}/{dailyLimit}')} tests used today.\n{i('Resets at midnight IST.')}",
            reply_markup=mainMenuKeyboard(),
            parse_mode=PM
        )
        await callback.answer()
        return

    data           = await state.get_data()
    await state.set_state(TestWizard.running)
    phone          = data["phone"]
    duration       = data["duration"]
    workers        = data["workers"]
    useProxy       = data.get("useProxy", False)
    workingProxies = data.get("workingProxies", [])

    db.incrementTestCount(userId)
    recordId               = db.startTestRecord(userId, phone, duration, workers)
    activeRecordIds[userId] = recordId

    runner = TesterRunner(
        phone=phone, duration=duration, workers=workers,
        useProxy=useProxy, proxyList=workingProxies,
    )
    activeRunners[userId] = runner
    summaryShown[userId]  = False

    _, testsNow, limitNow = db.canRunTest(userId)
    dashMsg = await callback.message.edit_text(
        f"{b('Test Running')}  {c(phone)}\n\n{i('Initializing...')}\n\n{c(f'Tests today: {testsNow}/{limitNow}')}",
        reply_markup=runningKeyboard(),
        parse_mode=PM
    )
    await callback.answer()
    await runner.start()

    task = asyncio.create_task(
        dashboardLoop(runner, dashMsg, phone, duration, userId, state)
    )
    dashboardTasks[userId] = task


@router.callback_query(F.data == "test:stop")
async def cbStopTest(callback: CallbackQuery, state: FSMContext) -> None:
    userId = callback.from_user.id
    runner = activeRunners.get(userId)
    if not runner:
        await callback.answer("No active test found.", show_alert=True)
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
    _saveHistory(userId, snap)

    activeRunners.pop(userId, None)
    summaryShown.pop(userId, None)
    activeRecordIds.pop(userId, None)
    await state.clear()

    summary = buildSummaryText(snap, runner.phone)
    try:
        await callback.message.edit_text(summary, reply_markup=finishedKeyboard(), parse_mode=PM)
    except Exception:
        await callback.message.answer(summary, reply_markup=finishedKeyboard(), parse_mode=PM)


@router.callback_query(F.data == "test:refresh")
async def cbRefresh(callback: CallbackQuery) -> None:
    userId = callback.from_user.id
    runner = activeRunners.get(userId)
    if not runner:
        await callback.answer("No active test.", show_alert=True)
        return
    snap    = runner.stats.snapshot()
    newText = buildDashboardText(snap, runner.phone, runner.duration)
    try:
        await callback.message.edit_text(newText, reply_markup=runningKeyboard(), parse_mode=PM)
    except Exception:
        pass
    await callback.answer("Refreshed.")


# ---------------------------------------------------------------------------
# Back keyboards for custom input steps
# ---------------------------------------------------------------------------

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def backToDurationKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="nav:duration")
    builder.adjust(1)
    return builder.as_markup()


def backToWorkersKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="nav:workers")
    builder.adjust(1)
    return builder.as_markup()