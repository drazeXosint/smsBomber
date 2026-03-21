from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import ADMIN_ID
from bot.services.database import db
from bot.utils import PM, b, i, c, hEsc

router = Router()


def isAdmin(userId: int) -> bool:
    return userId == ADMIN_ID


class NukeStates(StatesGroup):
    waitingPhone       = State()
    waitingGlobalPhone = State()


# ---------------------------------------------------------------------------
# Admin nuke — nuke a specific number as admin (no limits, max workers)
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:nuke")
async def cbAdminNuke(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(NukeStates.waitingPhone)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:menu")
    await callback.message.edit_text(
        f"{b('NUKE MODE')}\n\n"
        f"Enter the 10-digit target number.\n\n"
        f"{i('Nuke = 64 workers per API, burst mode permanent, no limits, 5 min duration.')}\n"
        f"{i('This will obliterate the target.')}\n\n"
        f"You can enter multiple numbers separated by comma.",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(NukeStates.waitingPhone))
async def handleNukePhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    from bot.handlers.test_flow import parsePhones, activeRunners, TestWizard
    phones = parsePhones(message.text or "")
    if not phones:
        await message.answer("Enter valid 10-digit number(s).")
        return
    await state.clear()

    userId = message.from_user.id
    if userId in activeRunners:
        builder = InlineKeyboardBuilder()
        builder.button(text="Stop Current", callback_data="test:stop")
        builder.button(text="Admin Menu",   callback_data="adm:menu")
        builder.adjust(1)
        await message.answer(
            f"A test is already running. Stop it first.",
            reply_markup=builder.as_markup()
        )
        return

    phoneStr = ", ".join(phones)
    builder  = InlineKeyboardBuilder()
    builder.button(text="LAUNCH NUKE",  callback_data=f"nuke:launch:{','.join(phones)}")
    builder.button(text="Cancel",       callback_data="adm:menu")
    builder.adjust(1)
    await message.answer(
        f"{b('CONFIRM NUKE')}\n\n"
        f"Targets   {c(hEsc(phoneStr))}\n"
        f"Duration  {c('5 minutes')}\n"
        f"Workers   {c('64 per API')}\n"
        f"Mode      {c('PERMANENT BURST')}\n\n"
        f"{i('This will fire every API at maximum power simultaneously.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# Global nuke — admin fires nuke that all users participate in
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "adm:global_nuke")
async def cbGlobalNuke(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(NukeStates.waitingGlobalPhone)
    builder = InlineKeyboardBuilder()
    builder.button(text="Cancel", callback_data="adm:menu")
    await callback.message.edit_text(
        f"{b('GLOBAL NUKE')}\n\n"
        f"Enter the target number.\n\n"
        f"{i('This will send a nuke launch message to ALL users.')}\n"
        f"{i('Every user who taps Launch will join the attack simultaneously.')}\n"
        f"{i('Combined firepower of all users at once.')}",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.message(StateFilter(NukeStates.waitingGlobalPhone))
async def handleGlobalNukePhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return
    from bot.handlers.test_flow import parsePhones
    phones = parsePhones(message.text or "")
    if not phones or len(phones) != 1:
        await message.answer("Enter exactly one 10-digit target number for global nuke.")
        return
    phone = phones[0]
    await state.clear()

    # Broadcast nuke invite to all users
    users  = db.getAllUsers(offset=0, limit=99999)
    sent   = 0
    failed = 0

    builder = InlineKeyboardBuilder()
    builder.button(text="LAUNCH NUKE", callback_data=f"nuke:launch:{phone}")
    builder.adjust(1)

    broadcastText = (
        f"{b('GLOBAL NUKE INCOMING')}\n\n"
        f"Target  {c(phone)}\n\n"
        f"{i('Admin has initiated a global nuke.')}\n"
        f"{i('Tap the button below to join the attack.')}\n"
        f"{i('Everyone fires simultaneously.')}"
    )

    status = await message.answer(f"Sending global nuke to {len(users)} users...")
    for u in users:
        if u["userId"] == message.from_user.id:
            continue
        if u["isBanned"]:
            continue
        try:
            await message.bot.send_message(
                u["userId"],
                broadcastText,
                reply_markup=builder.as_markup(),
                parse_mode=PM
            )
            sent += 1
        except Exception:
            failed += 1

    # Also launch for admin
    builder2 = InlineKeyboardBuilder()
    builder2.button(text="Admin Menu", callback_data="adm:menu")
    await status.edit_text(
        f"{b('Global Nuke Launched')}\n\n"
        f"Target   {c(phone)}\n"
        f"Invited  {c(str(sent))} users\n"
        f"Failed   {c(str(failed))}\n\n"
        f"{i('Users who tap Launch will join the attack.')}",
        reply_markup=builder2.as_markup(),
        parse_mode=PM
    )


# ---------------------------------------------------------------------------
# User nuke mode toggle in confirm screen
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "confirm:nuke_toggle")
async def cbNukeToggle(callback: CallbackQuery, state: FSMContext) -> None:
    """Toggle nuke mode on/off in the confirm screen."""
    data     = await state.get_data()
    nukeMode = not data.get("nukeMode", False)
    await state.update_data(nukeMode=nukeMode)

    from bot.handlers.test_flow import buildConfirmText, confirmKeyboard
    # Rebuild confirm keyboard with nuke toggle
    builder = InlineKeyboardBuilder()
    builder.button(text="Launch",                        callback_data="confirm:start")
    builder.button(text="Edit",                          callback_data="confirm:edit")
    nukeLabel = "Nuke Mode: ON" if nukeMode else "Nuke Mode: OFF"
    builder.button(text=nukeLabel,                       callback_data="confirm:nuke_toggle")
    builder.button(text="Cancel",                        callback_data="confirm:cancel")
    builder.adjust(2, 1, 1)

    data = await state.get_data()
    await callback.message.edit_text(
        buildConfirmText(data),
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer(f"Nuke mode {'ON' if nukeMode else 'OFF'}")