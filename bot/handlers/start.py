from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode

from bot.keyboards.menus import mainMenuKeyboard, backToMainKeyboard
from bot.services.database import db
from bot.config import ADMIN_ID
from bot.utils import mdEsc

router = Router()
MD = ParseMode.MARKDOWN_V2


def mainMenuText(userId: int) -> str:
    from bot.services.api_manager import apiManager
    u     = db.getUser(userId)
    total = len(apiManager.getMergedConfigs())
    if u:
        _, testsToday, dailyLimit = db.canRunTest(userId)
        usage = f"Tests today  `{testsToday}/{dailyLimit}`  ·  APIs loaded  `{total}`"
    else:
        usage = f"APIs loaded  `{total}`"
    return (
        f"*smsBomber*\n"
        f"{usage}\n\n"
        f"_by @drazeforce_"
    )


HELP_TEXT = (
    "*Help*\n\n"
    "*Start Test* — configure and launch an OTP flood against all loaded APIs\\.\n\n"
    "*Configuration* — set default workers and proxy preference\\.\n\n"
    "Dashboard updates live during a test\\. "
    "Use *Stop* to end early and see the final summary\\.\n\n"
    "_Daily limit resets at midnight IST\\._"
)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmdStart(message: Message, state: FSMContext) -> None:
    await state.clear()
    userId = message.from_user.id
    u = db.getUser(userId)
    if u and u["isBanned"]:
        await message.answer("Your account has been restricted\\.", parse_mode=MD)
        return
    await message.answer(mainMenuText(userId), reply_markup=mainMenuKeyboard(), parse_mode=MD)


@router.callback_query(F.data == "nav:main_menu")
async def cbMainMenu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        mainMenuText(callback.from_user.id),
        reply_markup=mainMenuKeyboard(),
        parse_mode=MD
    )
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cbHelp(callback: CallbackQuery) -> None:
    userId = callback.from_user.id
    u = db.getUser(userId)
    _, testsToday, dailyLimit = db.canRunTest(userId) if u else (False, 0, 0)
    text = HELP_TEXT
    if u:
        text += f"\n\n`Usage: {testsToday}/{dailyLimit} today`"
    await callback.message.edit_text(text, reply_markup=backToMainKeyboard(), parse_mode=MD)
    await callback.answer()