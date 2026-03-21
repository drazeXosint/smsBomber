from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

# Parse mode constant used everywhere
PM = "HTML"


def b(text: str) -> str:
    """Bold"""
    return f"<b>{text}</b>"


def i(text: str) -> str:
    """Italic"""
    return f"<i>{text}</i>"


def c(text: str) -> str:
    """Code/monospace"""
    return f"<code>{text}</code>"


def hEsc(text: str) -> str:
    """Escape HTML special characters"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def safeAnswer(callback: CallbackQuery, text: str = "", show_alert: bool = False) -> None:
    """Answer a callback query, silently ignoring stale query errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        err = str(e).lower()
        if "query is too old" in err or "query id is invalid" in err:
            pass
        else:
            raise
    except Exception:
        pass