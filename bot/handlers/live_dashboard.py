from __future__ import annotations

import asyncio
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import ADMIN_ID
from bot.utils import PM, b, i, c, hEsc

router = Router()

# This is imported and populated by test_flow.py
# We reference it directly to see all active runners
_activeRunners = None


def setActiveRunners(runners: dict) -> None:
    global _activeRunners
    _activeRunners = runners


def isAdmin(userId: int) -> bool:
    return userId == ADMIN_ID


def formatDuration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m, s = seconds // 60, seconds % 60
    return f"{m}m {s}s" if s else f"{m}m"


@router.callback_query(F.data == "adm:live")
async def cbLiveDashboard(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    from bot.handlers.test_flow import activeRunners
    from bot.services.database import db

    builder = InlineKeyboardBuilder()
    builder.button(text="Refresh", callback_data="adm:live")
    builder.button(text="Admin Menu", callback_data="adm:menu")
    builder.adjust(2)

    if not activeRunners:
        await callback.message.edit_text(
            f"{b('Live Dashboard')}\n\n{i('No tests running right now.')}",
            reply_markup=builder.as_markup(),
            parse_mode=PM
        )
        await callback.answer()
        return

    lines = [f"{b('Live Dashboard')}  {c(str(len(activeRunners)) + ' active tests')}\n"]

    for userId, runner in activeRunners.items():
        snap     = runner.stats.snapshot()
        u        = db.getUser(userId)
        name     = u["firstName"] if u else str(userId)
        un       = f"@{u['username']}" if u and u.get("username") else str(userId)
        elapsed  = int(snap["elapsed"])
        remaining = max(0, runner.duration - elapsed)
        pct      = int(min(elapsed / runner.duration * 100, 100)) if runner.duration > 0 else 0
        bar_filled = int(pct / 10)
        bar      = "█" * bar_filled + "░" * (10 - bar_filled)

        # Count API statuses
        active_apis = sum(1 for s in snap["perApi"].values() if s["status"] == "active")
        rl_apis     = sum(1 for s in snap["perApi"].values() if s["status"] == "ratelimited")
        dead_apis   = sum(1 for s in snap["perApi"].values() if s["status"] == "dead")

        lines.append(
            f"{b(hEsc(name))}  {c(hEsc(un))}\n"
            f"Phone      {c(runner.phone)}\n"
            f"Progress   <code>{bar}</code> {pct}%\n"
            f"Remaining  {c(formatDuration(remaining))}\n"
            f"Requests   {c(str(snap['totalReqs']))}  {i(str(snap['rps']) + ' r/s')}\n"
            f"Confirmed  {c(str(snap['confirmed']))}\n"
            f"APIs       {c(str(active_apis))} active  {rl_apis} RL  {dead_apis} dead\n"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()