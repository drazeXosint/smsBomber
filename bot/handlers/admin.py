from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import ADMIN_ID
from bot.services.database import db, IST
from bot.utils import PM, b, i, c, hEsc as _esc

router = Router()

USERS_PER_PAGE    = 8
BLACKLIST_PER_PAGE = 10


def isAdmin(userId: int) -> bool:
    return userId == ADMIN_ID


class AdminStates(StatesGroup):
    waitingSetLimit       = State()
    waitingBroadcast      = State()
    waitingGlobalLimit    = State()
    waitingBlacklistPhone  = State()
    waitingBlacklistReason = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def adminMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Users",         callback_data="adm:users:0")
    builder.button(text="Stats",         callback_data="adm:stats")
    builder.button(text="API Manager",   callback_data="aapi:menu")
    builder.button(text="Proxy Manager", callback_data="aprx:menu")
    builder.button(text="Reset All",     callback_data="adm:reset_all")
    builder.button(text="Global Limit",  callback_data="adm:global_limit")
    builder.button(text="Broadcast",     callback_data="adm:broadcast")
    builder.button(text="Blacklist",     callback_data="adm:blacklist:0")
    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


def usersListKeyboard(page: int, totalPages: int, users: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for u in users:
        name   = u["firstName"] or "Unknown"
        status = "BANNED" if u["isBanned"] else f"{u['testsToday']}/{u['dailyLimit']}"
        label  = f"{name}  -  {status}"
        builder.button(text=label, callback_data=f"adm:user:{u['userId']}")
    if page > 0:
        builder.button(text="Prev", callback_data=f"adm:users:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"adm:users:{page + 1}")
    builder.button(text="Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def userActionKeyboard(userId: int, isBanned: bool) -> InlineKeyboardMarkup:
    builder   = InlineKeyboardBuilder()
    banLabel  = "Unban" if isBanned else "Ban"
    builder.button(text=banLabel,       callback_data=f"adm:toggle_ban:{userId}")
    builder.button(text="Set Limit",    callback_data=f"adm:set_limit:{userId}")
    builder.button(text="Reset Today",  callback_data=f"adm:reset_user:{userId}")
    builder.button(text="History",      callback_data=f"adm:history:{userId}")
    builder.button(text="Back",         callback_data="adm:users:0")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def confirmResetAllKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Yes, reset all", callback_data="adm:confirm_reset_all")
    builder.button(text="Cancel",         callback_data="adm:menu")
    builder.adjust(2)
    return builder.as_markup()


def backToAdminKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Admin Menu", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def blacklistKeyboard(page: int, totalPages: int, entries: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start   = page * BLACKLIST_PER_PAGE
    for e in entries[start:start + BLACKLIST_PER_PAGE]:
        builder.button(text=f"Remove: {e['phone']}", callback_data=f"adm:bl_remove:{e['phone']}")
    builder.button(text="Add Number", callback_data="adm:bl_add")
    if page > 0:
        builder.button(text="Prev", callback_data=f"adm:blacklist:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"adm:blacklist:{page + 1}")
    builder.button(text="Back", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def formatUserDetail(u: dict) -> str:
    name = u["firstName"] or "Unknown"
    if u.get("lastName"):
        name += f" {u['lastName']}"
    username  = f"@{u['username']}" if u.get("username") else "no username"
    status    = "BANNED" if u["isBanned"] else "Active"
    joined    = datetime.fromtimestamp(u["joinedAt"], tz=IST).strftime("%d %b %Y")
    tests_str = f"{u['testsToday']} / {u['dailyLimit']} today"
    return (
        f"{b(_esc(name))}  {c(_esc(username))}\n\n"
        f"ID       {c(str(u['userId']))}\n"
        f"Status   {c(status)}\n"
        f"Tests    {c(tests_str)}\n"
        f"Joined   {c(joined)}"
    )


# ---------------------------------------------------------------------------
# /admin command
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def cmdAdmin(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        await message.answer("Unknown command.")
        return
    await state.clear()
    from bot.services.api_manager import apiManager
    total   = len(apiManager.getMergedConfigs())
    skipped = len(db.getSkippedApiNames())
    await message.answer(
        f"{b('Admin Panel')}\n\n"
        f"APIs     {c(str(total - skipped))} active  {c(str(skipped))} skipped\n"
        f"Users    {c(str(db.getUserCount()))}",
        reply_markup=adminMenuKeyboard(),
        parse_mode=PM
    )


@router.callback_query(F.data == "adm:menu")
async def cbAdminMenu(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    from bot.services.api_manager import apiManager
    total   = len(apiManager.getMergedConfigs())
    skipped = len(db.getSkippedApiNames())
    await callback.message.edit_text(
        f"{b('Admin Panel')}\n\n"
        f"APIs     {c(str(total - skipped))} active  {c(str(skipped))} skipped\n"
        f"Users    {c(str(db.getUserCount()))}",
        reply_markup=adminMenuKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:stats")
async def cbAdminStats(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    total           = db.getUserCount()
    users           = db.getAllUsers(offset=0, limit=9999)
    banned          = sum(1 for u in users if u["isBanned"])
    activeToday     = sum(1 for u in users if u["testsToday"] > 0)
    totalTestsToday = sum(u["testsToday"] for u in users)
    from bot.services.api_manager import apiManager
    apiTotal = len(apiManager.getMergedConfigs())
    skipped  = len(db.getSkippedApiNames())
    await callback.message.edit_text(
        f"{b('Bot Stats')}\n\n"
        f"Users total    {c(str(total))}\n"
        f"Banned         {c(str(banned))}\n"
        f"Active today   {c(str(activeToday))}\n"
        f"Tests today    {c(str(totalTestsToday))}\n\n"
        f"APIs active    {c(str(apiTotal - skipped))}\n"
        f"APIs skipped   {c(str(skipped))}",
        reply_markup=backToAdminKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Users list
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:users:"))
async def cbUsersList(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    page       = int(callback.data.split(":")[2])
    total      = db.getUserCount()
    totalPages = max(1, -(-total // USERS_PER_PAGE))
    users      = db.getAllUsers(offset=page * USERS_PER_PAGE, limit=USERS_PER_PAGE)
    if not users:
        await callback.message.edit_text("No users registered yet.", reply_markup=backToAdminKeyboard())
        await callback.answer()
        return
    await callback.message.edit_text(
        f"{b('Users')}  {c(f'{total} total  page {page+1}/{totalPages}')}\n\n{i('Tap a user to manage them.')}",
        reply_markup=usersListKeyboard(page, totalPages, users),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def cbUserDetail(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    await callback.message.edit_text(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Ban / Unban
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:toggle_ban:"))
async def cbToggleBan(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    if u["isBanned"]:
        db.unbanUser(userId)
        await callback.answer("User unbanned.")
    else:
        db.banUser(userId)
        await callback.answer("User banned.")
    u = db.getUser(userId)
    await callback.message.edit_text(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Set limit
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:set_limit:"))
async def cbSetLimit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    await state.set_state(AdminStates.waitingSetLimit)
    await state.update_data(targetUserId=userId)
    await callback.message.edit_text(
        f"{b('Set Daily Limit')}\n\nEnter new limit for user {c(str(userId))}.\nNumber between {c('0')} and {c('999')}.",
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingSetLimit)
async def handleSetLimit(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    try:
        limit = int(raw)
        if not 0 <= limit <= 999:
            raise ValueError
    except ValueError:
        await message.answer("Enter a number between 0 and 999.")
        return
    data         = await state.get_data()
    targetUserId = data["targetUserId"]
    db.setDailyLimit(targetUserId, limit)
    await state.clear()
    u = db.getUser(targetUserId)
    await message.answer(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(targetUserId, bool(u["isBanned"])),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Reset individual user
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:reset_user:"))
async def cbResetUser(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    db.resetUserTests(userId)
    await callback.answer("Daily count reset.")
    u = db.getUser(userId)
    await callback.message.edit_text(
        formatUserDetail(u),
        reply_markup=userActionKeyboard(userId, bool(u["isBanned"])),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Reset all users
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:reset_all")
async def cbResetAll(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await callback.message.edit_text(
        f"{b('Reset All Limits')}\n\nThis will reset today's test count for every user.\nAre you sure?",
        reply_markup=confirmResetAllKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:confirm_reset_all")
async def cbConfirmResetAll(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    db.resetAllTests()
    await callback.message.edit_text("Done. All daily counts reset.", reply_markup=backToAdminKeyboard())
    await callback.answer("All limits reset.")


# ---------------------------------------------------------------------------
# Global limit
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:global_limit")
async def cbGlobalLimit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingGlobalLimit)
    await callback.message.edit_text(
        f"{b('Set Global Daily Limit')}\n\nEnter a number to update the daily limit for every user.",
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingGlobalLimit)
async def handleGlobalLimit(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    try:
        limit = int(raw)
        if not 0 <= limit <= 999:
            raise ValueError
    except ValueError:
        await message.answer("Enter a number between 0 and 999.")
        return
    db.setGlobalDailyLimit(limit)
    await state.clear()
    await message.answer(
        f"Global limit updated. All users now have a daily limit of {c(str(limit))}.",
        reply_markup=backToAdminKeyboard(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# User history
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:history:"))
async def cbUserHistory(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId  = int(callback.data.split(":")[2])
    history = db.getUserHistory(userId, limit=10)
    if not history:
        await callback.answer("No test history for this user.", show_alert=True)
        return
    lines = [f"{b('Test History')}  {c(f'last {len(history)}')}\n"]
    for h in history:
        dt = datetime.fromtimestamp(h["startedAt"], tz=IST).strftime("%d %b %H:%M")
        lines.append(
            f"{c(dt)}  {h['phone']}  {h['duration']}s  "
            f"OTP {h['otpHits']}  REQ {h['totalReqs']}"
        )
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data=f"adm:user:{userId}")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:broadcast")
async def cbBroadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingBroadcast)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:menu")
    await callback.message.edit_text(
        f"{b('Broadcast Message')}\n\n"
        f"Type a message to send to all {c(str(db.getUserCount()))} users.\n\n"
        f"{i('Supports HTML formatting.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingBroadcast)
async def handleBroadcast(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    text  = message.text or message.caption or ""
    if not text.strip():
        await message.answer("Message cannot be empty.")
        return
    await state.clear()
    users   = db.getAllUsers(offset=0, limit=99999)
    total   = len(users)
    sent    = 0
    failed  = 0
    status  = await message.answer(f"Broadcasting to {total} users...")
    for u in users:
        try:
            await message.bot.send_message(u["userId"], text, parse_mode=PM)
            sent += 1
        except Exception:
            failed += 1
    await status.edit_text(
        f"{b('Broadcast Complete')}\n\n"
        f"Sent     {c(str(sent))}\n"
        f"Failed   {c(str(failed))}",
        reply_markup=backToAdminKeyboard(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Phone Blacklist
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:blacklist:"))
async def cbBlacklist(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    page       = int(callback.data.split(":")[2])
    entries    = db.getAllBlacklisted()
    totalPages = max(1, -(-len(entries) // BLACKLIST_PER_PAGE))
    if not entries:
        builder = InlineKeyboardBuilder()
        builder.button(text="Add Number", callback_data="adm:bl_add")
        builder.button(text="Back",       callback_data="adm:menu")
        builder.adjust(1)
        await callback.message.edit_text(
            f"{b('Phone Blacklist')}\n\nNo numbers blacklisted yet.",
            reply_markup=builder.as_markup(),
            parse_mode=PM
        )
        await callback.answer()
        return
    lines = [f"{b('Phone Blacklist')}  {c(str(len(entries)) + ' numbers')}\n"]
    start = page * BLACKLIST_PER_PAGE
    for e in entries[start:start + BLACKLIST_PER_PAGE]:
        reason = f"  - {e['reason']}" if e.get("reason") else ""
        dt     = datetime.fromtimestamp(e["addedAt"], tz=IST).strftime("%d %b %Y")
        lines.append(f"{e['phone']}{reason}  ({dt})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=blacklistKeyboard(page, totalPages, entries),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:bl_add")
async def cbBlAdd(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingBlacklistPhone)
    await callback.message.edit_text(
        f"{b('Add to Blacklist')}\n\nEnter the 10-digit phone number to permanently block.",
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingBlacklistPhone)
async def handleBlPhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    phone = (message.text or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        await message.answer("Enter exactly 10 digits.")
        return
    await state.update_data(blPhone=phone)
    await state.set_state(AdminStates.waitingBlacklistReason)
    await message.answer(
        f"Number: {c(phone)}\n\nEnter a reason (optional) or send  -  to skip.",
        parse_mode=PM
    )


@router.message(AdminStates.waitingBlacklistReason)
async def handleBlReason(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    reason = (message.text or "").strip()
    if reason == "-":
        reason = ""
    data  = await state.get_data()
    phone = data["blPhone"]
    db.blacklistPhone(phone, reason)
    await state.clear()
    reasonNote = f"\nReason: {reason}" if reason else ""
    builder = InlineKeyboardBuilder()
    builder.button(text="View Blacklist", callback_data="adm:blacklist:0")
    builder.button(text="Admin Menu",     callback_data="adm:menu")
    builder.adjust(1)
    await message.answer(
        f"Blacklisted.\n\n{c(phone)} permanently blocked.{reasonNote}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data.startswith("adm:bl_remove:"))
async def cbBlRemove(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    phone = callback.data.split(":", 2)[2]
    db.unblacklistPhone(phone)
    await callback.answer(f"Removed: {phone}")
    entries    = db.getAllBlacklisted()
    totalPages = max(1, -(-len(entries) // BLACKLIST_PER_PAGE))
    if not entries:
        builder = InlineKeyboardBuilder()
        builder.button(text="Add Number", callback_data="adm:bl_add")
        builder.button(text="Back",       callback_data="adm:menu")
        builder.adjust(1)
        await callback.message.edit_text(
            f"{b('Phone Blacklist')}\n\nBlacklist is now empty.",
            reply_markup=builder.as_markup(),
            parse_mode=PM
        )
        return
    lines = [f"{b('Phone Blacklist')}  {c(str(len(entries)) + ' numbers')}\n"]
    for e in entries[:BLACKLIST_PER_PAGE]:
        reason = f"  - {e['reason']}" if e.get("reason") else ""
        dt     = datetime.fromtimestamp(e["addedAt"], tz=IST).strftime("%d %b %Y")
        lines.append(f"{e['phone']}{reason}  ({dt})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=blacklistKeyboard(0, totalPages, entries),
        parse_mode=PM
    )