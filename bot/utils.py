"""Shared utilities for the bot."""


# HTML parse mode string — avoids importing aiogram at module level
PM = "HTML"


def b(t: str) -> str:
    """Bold."""
    return f"<b>{hEsc(t)}</b>"


def i(t: str) -> str:
    """Italic."""
    return f"<i>{hEsc(t)}</i>"


def c(t: str) -> str:
    """Inline code."""
    return f"<code>{hEsc(t)}</code>"


def hEsc(t: str) -> str:
    """Escape HTML special chars."""
    if not isinstance(t, str):
        t = str(t)
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Alias
def mdEsc(t: str) -> str:
    return hEsc(t)