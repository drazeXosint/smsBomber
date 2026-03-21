from __future__ import annotations

from bot.services.database import db
from bot.utils import PM

# ---------------------------------------------------------------------------
# Streak milestones — {days: (bonus_tests, label)}
# bonus_tests = -1 means UNLIMITED (set dailyLimit to 9999)
# ---------------------------------------------------------------------------

MILESTONES = {
    2:   (2,   "2-Day Streak"),
    3:   (3,   "3-Day Streak"),
    5:   (5,   "5-Day Streak"),
    7:   (5,   "1 Week Warrior"),
    10:  (7,   "10-Day Legend"),
    14:  (10,  "2 Week Beast"),
    21:  (15,  "3 Week Monster"),
    30:  (20,  "1 Month Destroyer"),
    50:  (30,  "50-Day God"),
    100: (-1,  "100-Day UNLIMITED"),
}

# Checkpoint emojis for streak display
def getCheckpoints(streak: int) -> str:
    milestonedays = sorted(MILESTONES.keys())
    lines = []
    for day in milestonedays:
        label  = MILESTONES[day][1]
        bonus  = MILESTONES[day][0]
        bonusStr = "UNLIMITED" if bonus == -1 else f"+{bonus} tests/day"
        if streak >= day:
            lines.append(f"✅ {day} days — {label} ({bonusStr})")
        else:
            lines.append(f"⬜ {day} days — {label} ({bonusStr})")
    return "\n".join(lines)


def getDailyBonus(streak: int) -> int:
    """Return total permanent daily bonus earned so far."""
    total = 0
    for day, (bonus, _) in MILESTONES.items():
        if streak >= day:
            if bonus == -1:
                return -1  # unlimited
            total += bonus
    return total


# ---------------------------------------------------------------------------
# Streak messages per milestone
# ---------------------------------------------------------------------------

MILESTONE_MESSAGES = {
    2: (
        "🔥 *2-Day Streak, {name}!*\n\n"
        "You're back! The bombing doesn't stop 😤\n\n"
        "🎁 *Reward:* +2 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "Keep it up! Next milestone at 3 days 👀"
    ),
    3: (
        "💥 *3 Days in a Row, {name}!*\n\n"
        "You're not playing around 😈 3 days straight!\n\n"
        "🎁 *Reward:* +3 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "Next stop: 5 days 🚀 Don't break the chain!"
    ),
    5: (
        "⚡ *5-Day BEAST MODE, {name}!*\n\n"
        "5 days no break! You're absolutely built different 🔥\n\n"
        "🎁 *Reward:* +5 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "7 days is the next checkpoint 👁️ Stay locked in!"
    ),
    7: (
        "🏆 *1 WEEK WARRIOR — {name}!*\n\n"
        "BRO. 7 days straight. That's an entire week of destruction! 💀\n"
        "You're officially a *1 Week Warrior* 🥇\n\n"
        "🎁 *Reward:* +5 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "10 days is coming 👿 Don't you dare stop!"
    ),
    10: (
        "😤 *10 DAYS?! {name} IS INSANE!*\n\n"
        "Double digits babyyy 🎉 10 days of pure chaos!\n"
        "The victim's phone is still ringing from day 1 💀\n\n"
        "🎁 *Reward:* +7 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "2 weeks is next. You got this 🔥"
    ),
    14: (
        "👑 *2 WEEK BEAST — {name}!*\n\n"
        "14 days. FOURTEEN. You haven't missed a single day 😤\n"
        "Half a month of absolute mayhem 🌪️\n\n"
        "🎁 *Reward:* +10 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "21 days next. You're already a legend 🏆"
    ),
    21: (
        "💣 *3 WEEK MONSTER — {name}!*\n\n"
        "21 DAYS STRAIGHT. You're not human 👾\n"
        "3 weeks of non-stop bombing. Victims are shaking 😂\n\n"
        "🎁 *Reward:* +15 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "30 days = 1 month destroyer. Almost there 💪"
    ),
    30: (
        "🌋 *1 MONTH DESTROYER — {name}!*\n\n"
        "30 DAYS. ONE ENTIRE MONTH. 🎊🎊🎊\n"
        "You've been bombing for a full month without stopping.\n"
        "You are literally unstoppable 🔱\n\n"
        "🎁 *Reward:* +20 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "50 days is the next GOD tier. Let's gooo 🚀🚀🚀"
    ),
    50: (
        "☠️ *50-DAY GOD — {name}!*\n\n"
        "50 DAYS WITHOUT MISSING A SINGLE DAY 😱\n"
        "At this point you ARE the smsBomber 💀\n"
        "Victims fear your name. Servers go down when you log in 😤\n\n"
        "🎁 *Reward:* +30 tests/day permanently added!\n"
        "📊 Your new daily limit: *{newLimit} tests/day*\n\n"
        "{checkpoints}\n\n"
        "100 days = UNLIMITED POWER. The final boss 👁️"
    ),
    100: (
        "⚜️ *100-DAY UNLIMITED GOD — {name}!*\n\n"
        "1️⃣0️⃣0️⃣ DAYS. THE CENTURY. THE LEGEND. 🏆🏆🏆\n\n"
        "You have achieved what no one else has.\n"
        "You are the *smsBomber* in its purest form 💥\n"
        "You bombed. Every. Single. Day. For 100 days. 🫡\n\n"
        "🎁 *ULTIMATE REWARD:* UNLIMITED tests/day FOREVER!\n"
        "📊 Your new daily limit: *∞ UNLIMITED*\n\n"
        "{checkpoints}\n\n"
        "There is nothing above this. You are the 👑 KING 👑"
    ),
}

# Non-milestone daily message (keep them coming back)
DAILY_MESSAGES = [
    "🔥 *Day {streak}, {name}!*\n\nKeep that streak alive! You're on fire 🚀\nNext milestone: *{nextMilestone} days* — only {daysLeft} more to go!\n\n📊 Current limit: *{limit} tests/day*",
    "💪 *Streak: {streak} days, {name}!*\n\nConsistency is key and you got it 😤\nNext reward at *{nextMilestone} days* — {daysLeft} days away!\n\n📊 Current limit: *{limit} tests/day*",
    "⚡ *{name} is on a {streak}-day run!*\n\nThe bombing never stops 😈\nHit *{nextMilestone} days* for your next reward — {daysLeft} more!\n\n📊 Current limit: *{limit} tests/day*",
    "😤 *{streak} days straight, {name}!*\n\nNothing can stop you 💥\n{daysLeft} more days until your next bonus at *{nextMilestone} days*!\n\n📊 Current limit: *{limit} tests/day*",
]

BROKEN_MESSAGES = [
    "💔 *Oh no {name}... you broke your streak!*\n\nYou missed a day and your streak reset to 1 😭\nBut don't worry — start fresh and climb back up 💪\n\n🔥 New streak: *1 day*\nFirst milestone: *2 days* — just 1 more day away!",
    "😱 *{name}!! YOUR STREAK IS GONE!*\n\nYou missed yesterday and it reset to 1 💀\nNo worries — every legend has a restart story 😤\nGet back on it TODAY 🔥\n\n🔥 New streak: *1 day*",
    "🥲 *Streak broken, {name}...*\n\nIt happens to the best of us 💔\nYou had a good run though! Now let's build an even bigger one 🚀\n\n🔥 New streak: *1 day*\nCome back tomorrow and keep it going!",
]

FIRST_TIME_MESSAGE = (
    "👋 *Welcome to smsBomber, {name}!*\n\n"
    "You just started your streak journey! 🎯\n\n"
    "🔥 Streak: *1 day*\n"
    "📊 Daily limit: *{limit} tests/day*\n\n"
    "Come back every day to earn permanent bonus tests!\n\n"
    "{checkpoints}\n\n"
    "First milestone: *2 days* — just come back tomorrow! 🚀"
)

import random


def _formatMsg(template: str, name: str, streak: int, limit: int, newLimit: int = 0) -> str:
    milestonedays = sorted(MILESTONES.keys())
    nextMilestone = next((d for d in milestonedays if d > streak), milestonedays[-1])
    daysLeft      = max(0, nextMilestone - streak)
    checkpoints   = getCheckpoints(streak)
    return (
        template
        .replace("{name}", name)
        .replace("{streak}", str(streak))
        .replace("{limit}", str(limit))
        .replace("{newLimit}", str(newLimit if newLimit else limit))
        .replace("{nextMilestone}", str(nextMilestone))
        .replace("{daysLeft}", str(daysLeft))
        .replace("{checkpoints}", checkpoints)
    )


# ---------------------------------------------------------------------------
# Main streak update function — call on every user interaction
# ---------------------------------------------------------------------------

async def updateStreak(userId: int, bot) -> None:
    """
    Update user streak and send a message if anything changed.
    Call this on every user message/callback.
    """
    from bot.services.database import getIstToday
    from datetime import datetime, timedelta
    from bot.services.database import IST

    u = db.getUser(userId)
    if not u:
        return

    today     = getIstToday()
    yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
    last      = u.get("lastStreakDate", "")
    streak    = u.get("streakDays", 0)
    name      = u.get("firstName") or u.get("username") or "Bomber"
    limit     = u.get("dailyLimit", 10)

    # Already updated today — do nothing
    if last == today:
        return

    isFirstTime  = streak == 0 and last == ""
    isNewDay     = last == yesterday
    isBroken     = last != yesterday and last != "" and last != today

    if isFirstTime:
        # First ever use
        streak   = 1
        newLimit = limit
        db._execute(
            "UPDATE users SET streakDays=1, lastStreakDate=? WHERE userId=?",
            (today, userId)
        )
        msg = _formatMsg(FIRST_TIME_MESSAGE, name, streak, limit, newLimit)

    elif isNewDay:
        # Continuing streak
        streak += 1
        db._execute(
            "UPDATE users SET streakDays=streakDays+1, lastStreakDate=? WHERE userId=?",
            (today, userId)
        )

        # Check if this streak hits a milestone
        if streak in MILESTONES:
            bonus, _ = MILESTONES[streak]
            if bonus == -1:
                # Unlimited
                newLimit = 9999
                db._execute("UPDATE users SET dailyLimit=9999 WHERE userId=?", (userId,))
            else:
                newLimit = limit + bonus
                db._execute(
                    "UPDATE users SET dailyLimit=dailyLimit+? WHERE userId=?",
                    (bonus, userId)
                )
            # Update cache
            if userId in db._userCache:
                db._userCache[userId]["streakDays"]   = streak
                db._userCache[userId]["lastStreakDate"] = today
                db._userCache[userId]["dailyLimit"]    = newLimit
            db._forceSync()
            template = MILESTONE_MESSAGES[streak]
            msg      = _formatMsg(template, name, streak, newLimit, newLimit)
        else:
            # Update cache
            if userId in db._userCache:
                db._userCache[userId]["streakDays"]    = streak
                db._userCache[userId]["lastStreakDate"] = today
            newLimit = db.getUser(userId)["dailyLimit"]
            template = random.choice(DAILY_MESSAGES)
            msg      = _formatMsg(template, name, streak, newLimit)

    elif isBroken:
        # Missed a day — reset
        streak   = 1
        newLimit = limit
        db._execute(
            "UPDATE users SET streakDays=1, lastStreakDate=? WHERE userId=?",
            (today, userId)
        )
        if userId in db._userCache:
            db._userCache[userId]["streakDays"]    = 1
            db._userCache[userId]["lastStreakDate"] = today
        msg = _formatMsg(random.choice(BROKEN_MESSAGES), name, streak, limit)
    else:
        return

    db._forceSync()

    # Send the message
    try:
        await bot.send_message(userId, msg, parse_mode="Markdown")
    except Exception:
        try:
            await bot.send_message(userId, msg, parse_mode=None)
        except Exception:
            pass