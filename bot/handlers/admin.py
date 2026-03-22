from __future__ import annotations

from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import ADMIN_ID
from bot.services.database import db, IST
from bot.utils import PM, b, i, c, hEsc as _esc

router = Router()

USERS_PER_PAGE     = 8
BLACKLIST_PER_PAGE = 10
DM_USERS_PER_PAGE  = 8


def isAdmin(userId: int) -> bool:
    return userId == ADMIN_ID


class AdminStates(StatesGroup):
    waitingSetLimit        = State()
    waitingBroadcast       = State()
    waitingGlobalLimit     = State()
    waitingBlacklistPhone  = State()
    waitingBlacklistReason = State()
    waitingDmMessage       = State()
    waitingUserSearch      = State()
    waitingMaintenanceMsg  = State()


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def adminMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Users",          callback_data="adm:users:0")
    builder.button(text="Search User",    callback_data="adm:search")
    builder.button(text="Stats",          callback_data="adm:stats")
    builder.button(text="Analytics",      callback_data="adm:analytics")
    builder.button(text="API Manager",    callback_data="aapi:menu")
    builder.button(text="Proxy Manager",  callback_data="aprx:menu")
    builder.button(text="Maintenance",    callback_data="adm:maintenance")
    builder.button(text="DM User",        callback_data="adm:dmlist:0")
    builder.button(text="Reset All",      callback_data="adm:reset_all")
    builder.button(text="Global Limit",   callback_data="adm:global_limit")
    builder.button(text="Broadcast",      callback_data="adm:broadcast")
    builder.button(text="Blacklist",      callback_data="adm:blacklist:0")
    builder.button(text="Nuke",           callback_data="adm:nuke")
    builder.button(text="Live Dashboard", callback_data="adm:live")
    builder.adjust(2, 2, 2, 2, 2, 2, 1, 1)
    return builder.as_markup()


def usersListKeyboard(page: int, totalPages: int, users: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for u in users:
        name   = u["firstName"] or "Unknown"
        total  = u.get("testsTotal", u["testsToday"])
        status = "BANNED" if u["isBanned"] else f"{total} tests"
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
    builder  = InlineKeyboardBuilder()
    banLabel = "Unban" if isBanned else "Ban"
    builder.button(text=banLabel,      callback_data=f"adm:toggle_ban:{userId}")
    builder.button(text="Set Limit",   callback_data=f"adm:set_limit:{userId}")
    builder.button(text="Reset Today", callback_data=f"adm:reset_user:{userId}")
    builder.button(text="History",     callback_data=f"adm:history:{userId}")
    builder.button(text="DM User",     callback_data=f"adm:dm:{userId}")
    builder.button(text="Back",        callback_data="adm:users:0")
    builder.adjust(2, 2, 1, 1)
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
    today_str = f"{u['testsToday']} / {u['dailyLimit']} today"
    total     = u.get("testsTotal", u["testsToday"])
    streak    = u.get("streakDays", 0)
    bonus     = u.get("bonusTests", 0)
    return (
        f"{b(_esc(name))}  {c(_esc(username))}\n\n"
        f"ID        {c(str(u['userId']))}\n"
        f"Status    {c(status)}\n"
        f"Today     {c(today_str)}\n"
        f"All time  {c(str(total))} tests\n"
        f"Streak    {c(str(streak))} days\n"
        f"Bonus     {c(str(bonus))} tests\n"
        f"Joined    {c(joined)}"
    )


# ---------------------------------------------------------------------------
# /admin command
# ---------------------------------------------------------------------------

@router.message(Command("cleanup"))
async def cmdCleanup(message: Message) -> None:
    if not isAdmin(message.from_user.id):
        return
    # Delete all test records with 0 requests (bad records from crashed nuke)
    db._execute(
        "DELETE FROM testHistory WHERE totalReqs = 0 AND otpHits = 0"
    )
    db._forceSync()
    await message.answer("✅ Cleaned up all empty test records.")
    if not isAdmin(message.from_user.id):
        await message.answer("Unknown command.")
        return
    await state.clear()
    from bot.services.api_manager import apiManager
    total   = len(apiManager.getMergedConfigs())
    skipped = len(db.getSkippedApiNames())
    maint   = "ON" if db.isMaintenanceMode() else "OFF"
    await message.answer(
        f"{b('Admin Panel')}\n\n"
        f"APIs        {c(str(total - skipped))} active  {c(str(skipped))} skipped\n"
        f"Users       {c(str(db.getUserCount()))}\n"
        f"Maintenance {c(maint)}",
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
    maint   = "ON" if db.isMaintenanceMode() else "OFF"
    await callback.message.edit_text(
        f"{b('Admin Panel')}\n\n"
        f"APIs        {c(str(total - skipped))} active  {c(str(skipped))} skipped\n"
        f"Users       {c(str(db.getUserCount()))}\n"
        f"Maintenance {c(maint)}",
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
    totalTestsEver  = sum(u.get("testsTotal", 0) for u in users)
    from bot.services.api_manager import apiManager
    apiTotal = len(apiManager.getMergedConfigs())
    skipped  = len(db.getSkippedApiNames())
    await callback.message.edit_text(
        f"{b('Bot Stats')}\n\n"
        f"Users total    {c(str(total))}\n"
        f"Banned         {c(str(banned))}\n"
        f"Active today   {c(str(activeToday))}\n"
        f"Tests today    {c(str(totalTestsToday))}\n"
        f"Tests all time {c(str(totalTestsEver))}\n\n"
        f"APIs active    {c(str(apiTotal - skipped))}\n"
        f"APIs skipped   {c(str(skipped))}",
        reply_markup=backToAdminKeyboard(),
        parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:analytics")
async def cbAnalytics(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    stats   = db.getAnalytics()
    users   = db.getAllUsers(offset=0, limit=9999)
    banned  = sum(1 for u in users if u["isBanned"])
    active  = sum(1 for u in users if u["testsToday"] > 0)
    topApis = db.getTopApis(limit=5)
    apiLines = []
    for a in topApis:
        rate = f"{round(a['totalOtps'] / a['totalReqs'] * 100, 1)}%" if a["totalReqs"] > 0 else "0%"
        apiLines.append(
            f"<code>{_esc(a['name'][:16]):<16}</code>  "
            f"{c(str(a['totalOtps']))} otp  {a['totalReqs']} req  {rate}"
        )
    apiBlock = "\n".join(apiLines) if apiLines else i("No data yet.")
    builder = InlineKeyboardBuilder()
    builder.button(text="Full API Stats", callback_data="adm:apistats:0")
    builder.button(text="Leaderboard",    callback_data="adm:leaderboard")
    builder.button(text="Admin Menu",     callback_data="adm:menu")
    builder.adjust(2, 1)
    await callback.message.edit_text(
        f"{b('Analytics')}\n\n"
        f"Today\n"
        f"Tests run    {c(str(stats['todayTests']))}\n"
        f"Requests     {c(str(stats['todayReqs']))}\n\n"
        f"All time\n"
        f"Tests run    {c(str(stats['totalTests']))}\n"
        f"Requests     {c(str(stats['totalReqs']))}\n"
        f"OTPs         {c(str(stats['totalOtps']))}\n\n"
        f"Users\n"
        f"Total        {c(str(len(users)))}\n"
        f"Active today {c(str(active))}\n"
        f"Banned       {c(str(banned))}\n\n"
        f"{b('Top APIs')}\n{apiBlock}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


APISTATS_PER_PAGE = 8


@router.callback_query(F.data.startswith("adm:apistats:"))
async def cbApiStats(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    page    = int(callback.data.split(":")[2])
    allApis = db.getAllApiStats()
    if not allApis:
        builder = InlineKeyboardBuilder()
        builder.button(text="Back", callback_data="adm:analytics")
        await callback.message.edit_text(
            f"{b('API Stats')}\n\n{i('No data yet. Run some tests first.')}",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        await callback.answer()
        return
    total      = len(allApis)
    totalPages = max(1, -(-total // APISTATS_PER_PAGE))
    start      = page * APISTATS_PER_PAGE
    pageApis   = allApis[start:start + APISTATS_PER_PAGE]
    lines = [f"{b('API Stats')}  {c(f'page {page+1}/{totalPages}')}\n"]
    for a in pageApis:
        rate = f"{round(a['totalOtps'] / a['totalReqs'] * 100, 1)}%" if a["totalReqs"] > 0 else "0%"
        lines.append(
            f"<code>{_esc(a['name'][:16]):<16}</code>  "
            f"{c(str(a['totalOtps']))} otp  {a['totalReqs']} req  {c(rate)}"
        )
    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text="Prev", callback_data=f"adm:apistats:{page-1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"adm:apistats:{page+1}")
    builder.button(text="Back", callback_data="adm:analytics")
    builder.adjust(2, 1)
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:leaderboard")
async def cbLeaderboard(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    topUsers = db.getTopUsers(limit=10)
    lines    = [f"{b('Top Users')}\n"]
    medals   = ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10."]
    for n, u in enumerate(topUsers):
        name = u["firstName"] or "Unknown"
        un   = f"@{u['username']}" if u.get("username") else str(u["userId"])
        lines.append(
            f"{medals[n]}  {_esc(name)}  {c(_esc(un))}\n"
            f"    {u.get('testsTotal', 0)} tests  {u.get('totalOtpHits', 0)} OTPs"
        )
    builder = InlineKeyboardBuilder()
    builder.button(text="Back", callback_data="adm:analytics")
    await callback.message.edit_text(
        "\n".join(lines) if topUsers else f"{b('Leaderboard')}\n\n{i('No data yet.')}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Maintenance mode
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:maintenance")
async def cbMaintenance(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    enabled = db.isMaintenanceMode()
    msg     = db.getMaintenanceMessage()
    label   = "Disable Maintenance" if enabled else "Enable Maintenance"
    status  = "ON" if enabled else "OFF"
    builder = InlineKeyboardBuilder()
    builder.button(text=label,         callback_data="adm:maintenance_toggle")
    builder.button(text="Set Message", callback_data="adm:maintenance_msg")
    builder.button(text="Admin Menu",  callback_data="adm:menu")
    builder.adjust(2, 1)
    await callback.message.edit_text(
        f"{b('Maintenance Mode')}\n\n"
        f"Status   {c(status)}\n\n"
        f"Message:\n{i(_esc(msg))}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data == "adm:maintenance_toggle")
async def cbMaintenanceToggle(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    current = db.isMaintenanceMode()
    db.setMaintenanceMode(not current)
    status = "enabled" if not current else "disabled"
    await callback.answer(f"Maintenance mode {status}.")
    await cbMaintenance(callback)


@router.callback_query(F.data == "adm:maintenance_msg")
async def cbMaintenanceMsgPrompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingMaintenanceMsg)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:maintenance")
    await callback.message.edit_text(
        f"{b('Set Maintenance Message')}\n\nType the message users will see.",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingMaintenanceMsg)
async def handleMaintenanceMsg(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    msg = (message.text or "").strip()
    if not msg:
        await message.answer("Message cannot be empty.")
        return
    db.setMaintenanceMessage(msg)
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="Maintenance Settings", callback_data="adm:maintenance")
    await message.answer("Maintenance message updated.", reply_markup=builder.as_markup())


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
# User search
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:search")
async def cbSearch(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(AdminStates.waitingUserSearch)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:menu")
    await callback.message.edit_text(
        f"{b('Search Users')}\n\nEnter a username, name, or user ID.",
        reply_markup=builder.as_markup(), parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingUserSearch)
async def handleUserSearch(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    query   = (message.text or "").strip()
    results = db.searchUsers(query)
    await state.clear()
    if not results:
        builder = InlineKeyboardBuilder()
        builder.button(text="Search Again", callback_data="adm:search")
        builder.button(text="Admin Menu",   callback_data="adm:menu")
        builder.adjust(1)
        await message.answer(
            f"No users found for {c(_esc(query))}.",
            reply_markup=builder.as_markup(), parse_mode=PM
        )
        return
    builder = InlineKeyboardBuilder()
    for u in results[:10]:
        name  = u["firstName"] or "Unknown"
        total = u.get("testsTotal", 0)
        label = f"{name}  -  {total} tests"
        builder.button(text=label, callback_data=f"adm:user:{u['userId']}")
    builder.button(text="Admin Menu", callback_data="adm:menu")
    builder.adjust(1)
    await message.answer(
        f"{b('Search Results')}  {c(str(len(results)) + ' found')}\n\n{i('Tap a user to manage them.')}",
        reply_markup=builder.as_markup(), parse_mode=PM
    )


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
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode=PM
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
    text = message.text or message.caption or ""
    if not text.strip():
        await message.answer("Message cannot be empty.")
        return
    await state.clear()
    users  = db.getAllUsers(offset=0, limit=99999)
    total  = len(users)
    sent   = 0
    failed = 0
    status = await message.answer(f"Broadcasting to {total} users...")
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
# DM a specific user
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("adm:dmlist:"))
async def cbDmList(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    page       = int(callback.data.split(":")[2])
    total      = db.getUserCount()
    totalPages = max(1, -(-total // DM_USERS_PER_PAGE))
    users      = db.getAllUsers(offset=page * DM_USERS_PER_PAGE, limit=DM_USERS_PER_PAGE)
    if not users:
        await callback.message.edit_text("No users found.", reply_markup=backToAdminKeyboard())
        await callback.answer()
        return
    builder = InlineKeyboardBuilder()
    for u in users:
        name   = u["firstName"] or "Unknown"
        un     = f"@{u['username']}" if u.get("username") else str(u["userId"])
        status = " [banned]" if u["isBanned"] else ""
        builder.button(text=f"{name}  {un}{status}", callback_data=f"adm:dm:{u['userId']}")
    if page > 0:
        builder.button(text="Prev", callback_data=f"adm:dmlist:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"adm:dmlist:{page + 1}")
    builder.button(text="Back", callback_data="adm:menu")
    builder.adjust(1)
    await callback.message.edit_text(
        f"{b('Send DM')}  {c(f'{total} users  page {page+1}/{totalPages}')}\n\n"
        f"{i('Select a user to send them a private message.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:dm:"))
async def cbDmSelectUser(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    userId = int(callback.data.split(":")[2])
    u      = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return
    name = u["firstName"] or "Unknown"
    if u.get("lastName"):
        name += f" {u['lastName']}"
    un = f"@{u['username']}" if u.get("username") else "no username"
    await state.set_state(AdminStates.waitingDmMessage)
    await state.update_data(dmTargetId=userId)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:dmlist:0")
    await callback.message.edit_text(
        f"{b('Send DM')}\n\n"
        f"To       {c(_esc(name))}\n"
        f"Username {c(_esc(un))}\n"
        f"ID       {c(str(userId))}\n\n"
        f"Type your message below.\n{i('Supports HTML formatting.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(AdminStates.waitingDmMessage)
async def handleDmMessage(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    text = message.text or message.caption or ""
    if not text.strip():
        await message.answer("Message cannot be empty.")
        return
    data     = await state.get_data()
    targetId = data["dmTargetId"]
    await state.clear()
    u    = db.getUser(targetId)
    name = (u["firstName"] or "Unknown") if u else str(targetId)
    try:
        await message.bot.send_message(targetId, text, parse_mode=PM)
        builder = InlineKeyboardBuilder()
        builder.button(text="Send Another DM", callback_data="adm:dmlist:0")
        builder.button(text="Admin Menu",      callback_data="adm:menu")
        builder.adjust(1)
        await message.answer(
            f"{b('DM Sent')}\n\nMessage delivered to {c(_esc(name))}.",
            reply_markup=builder.as_markup(),
            parse_mode=PM
        )
    except Exception as e:
        builder = InlineKeyboardBuilder()
        builder.button(text="Admin Menu", callback_data="adm:menu")
        await message.answer(
            f"{b('Failed')}\n\nCould not send to {c(_esc(name))}.\n"
            f"{i('They may have blocked the bot.')}\n\n"
            f"Error: {c(_esc(str(e)[:80]))}",
            reply_markup=builder.as_markup(),
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
