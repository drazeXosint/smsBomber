from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.menus import mainMenuKeyboard
from bot.services.database import db
from bot.config import ADMIN_ID
from bot.utils import PM, b, i, c, hEsc

router = Router()


# ---------------------------------------------------------------------------
# Streak trigger — call on every user touch
# ---------------------------------------------------------------------------

async def triggerStreak(userId: int, bot) -> None:
    """Fire-and-forget streak update. Never blocks the handler."""
    try:
        from streak import updateStreak
        asyncio.create_task(updateStreak(userId, bot))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmdStart(message: Message) -> None:
    userId   = message.from_user.id
    username = message.from_user.username
    firstName = message.from_user.first_name or "Bomber"
    lastName  = message.from_user.last_name

    isNew = db.registerUser(userId, username, firstName, lastName)

    # Notify admin of new user
    if isNew:
        try:
            await message.bot.send_message(
                ADMIN_ID,
                f"👤 {b('New User')}\n\n"
                f"Name     {c(hEsc(firstName))}\n"
                f"Username {c('@' + hEsc(username) if username else 'none')}\n"
                f"ID       {c(str(userId))}\n\n"
                f"Total users: {c(str(db.getUserCount()))}",
                parse_mode=PM
            )
        except Exception:
            pass

    # Check maintenance
    if db.isMaintenanceMode() and userId != ADMIN_ID:
        await message.answer(
            f"🔧 {b('Maintenance Mode')}\n\n{db.getMaintenanceMessage()}",
            parse_mode=PM
        )
        return

    # Check referral
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrerId = int(args[1].replace("ref_", ""))
            if db.applyReferral(referrerId, userId):
                try:
                    refUser = db.getUser(referrerId)
                    refName = refUser["firstName"] if refUser else "Someone"
                    await message.bot.send_message(
                        referrerId,
                        f"🎉 {b('New Referral!')}\n\n"
                        f"{hEsc(firstName)} joined using your link!\n"
                        f"You got {c('+3 bonus tests')} 🔥",
                        parse_mode=PM
                    )
                except Exception:
                    pass
        except Exception:
            pass

    u      = db.getUser(userId)
    streak = u.get("streakDays", 0) if u else 0
    _, testsToday, effectiveLimit = db.canRunTest(userId)

    streakStr = f"🔥 {streak} day streak" if streak > 1 else "🔥 Day 1 — start your streak!"

    greeting = (
        f"{b('smsBomber')}\n\n"
        f"Hey {hEsc(firstName)}! 👋\n\n"
        f"{streakStr}\n"
        f"Tests today: {c(f'{testsToday}/{effectiveLimit}')}\n\n"
        f"{i('Select an option below.')}"
    )

    # Send menu FIRST — instant response
    await message.answer(greeting, reply_markup=mainMenuKeyboard(), parse_mode=PM)

    # Fire streak AFTER menu is shown — non-blocking
    await triggerStreak(userId, message.bot)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

@router.message(Command("help"))
async def cmdHelp(message: Message) -> None:
    await triggerStreak(message.from_user.id, message.bot)
    await message.answer(
        f"{b('Help')}\n\n"
        f"Start Test — bomb a target number\n"
        f"My History — view past tests\n"
        f"Favorites — save numbers for quick access\n"
        f"Presets — save full test configs\n"
        f"Schedule — schedule a test for later\n"
        f"My Stats — your stats and streak\n"
        f"Referral — invite friends for bonus tests\n\n"
        f"Use /admin if you are the admin.\n\n"
        f"{i('Come back every day to grow your streak and earn more daily tests!')}",
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Main menu nav
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "nav:main_menu")
async def cbMainMenu(callback: CallbackQuery) -> None:
    await callback.answer()  # answer instantly — stop spinner
    userId = callback.from_user.id

    # Streak on every interaction
    await triggerStreak(userId, callback.bot)

    if db.isMaintenanceMode() and userId != ADMIN_ID:
        await callback.answer(db.getMaintenanceMessage(), show_alert=True)
        return

    u      = db.getUser(userId)
    streak = u.get("streakDays", 0) if u else 0
    _, testsToday, effectiveLimit = db.canRunTest(userId)
    firstName = u["firstName"] if u else "Bomber"

    streakStr = f"🔥 {streak} day streak" if streak > 1 else "🔥 Day 1 — start your streak!"

    await callback.message.edit_text(
        f"{b('smsBomber')}\n\n"
        f"Hey {hEsc(firstName)}! 👋\n\n"
        f"{streakStr}\n"
        f"Tests today: {c(f'{testsToday}/{effectiveLimit}')}\n\n"
        f"{i('Select an option below.')}",
        reply_markup=mainMenuKeyboard(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# My Stats
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:stats")
async def cbMyStats(callback: CallbackQuery) -> None:
    await callback.answer()
    userId = callback.from_user.id
    await triggerStreak(userId, callback.bot)

    u = db.getUser(userId)
    if not u:
        await callback.answer("User not found.", show_alert=True)
        return

    streak      = u.get("streakDays", 0)
    totalTests  = u.get("testsTotal", 0)
    totalReqs   = u.get("totalReqs", 0)
    totalOtps   = u.get("totalOtpHits", 0)
    referrals   = db.getReferralCount(userId)
    limit       = u.get("dailyLimit", 10)
    bonusTests  = u.get("bonusTests", 0)
    _, today, effectiveLimit = db.canRunTest(userId)

    otpRate = f"{round(totalOtps / totalReqs * 100, 1)}%" if totalReqs > 0 else "0%"

    # Next milestone
    from streak import MILESTONES
    milestonedays = sorted(MILESTONES.keys())
    nextMilestone = next((d for d in milestonedays if d > streak), None)
    nextStr = f"{nextMilestone - streak} more days → next reward" if nextMilestone else "ALL MILESTONES UNLOCKED 🏆"

    limitStr = "∞ UNLIMITED" if limit >= 9999 else str(effectiveLimit)

    builder = InlineKeyboardBuilder()
    builder.button(text="Main Menu", callback_data="nav:main_menu")

    await callback.message.edit_text(
        f"{b('My Stats')}\n\n"
        f"🔥 Streak       {c(str(streak))} days\n"
        f"📈 Next reward  {i(nextStr)}\n\n"
        f"🧪 Tests today  {c(f'{today}/{limitStr}')}\n"
        f"📦 Total tests  {c(str(totalTests))}\n"
        f"📡 Total reqs   {c(str(totalReqs))}\n"
        f"✅ OTPs hit     {c(str(totalOtps))} ({otpRate})\n"
        f"👥 Referrals    {c(str(referrals))}\n"
        f"🎁 Bonus tests  {c(str(bonusTests))}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Referral
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "menu:referral")
async def cbReferral(callback: CallbackQuery) -> None:
    await callback.answer()
    userId = callback.from_user.id
    await triggerStreak(userId, callback.bot)

    code    = db.getReferralCode(userId)
    count   = db.getReferralCount(userId)
    botInfo = await callback.bot.get_me()
    link    = f"https://t.me/{botInfo.username}?start={code}"

    builder = InlineKeyboardBuilder()
    builder.button(text="Main Menu", callback_data="nav:main_menu")

    await callback.message.edit_text(
        f"{b('Referral Program')}\n\n"
        f"Share your link and earn bonus tests!\n\n"
        f"Your link:\n{c(link)}\n\n"
        f"Referrals:  {c(str(count))}\n"
        f"You earn:   {c('+3 tests')} per referral\n"
        f"They get:   {c('+1 test')} on signup\n\n"
        f"{i('Bonuses are permanent and stack with streaks!')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
