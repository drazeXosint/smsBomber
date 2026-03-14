from __future__ import annotations

import json
import asyncio
import random
import string
from typing import Optional, List

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

from bot.config import ADMIN_ID
from bot.services.database import db
from bot.services.api_manager import apiManager
from bot.services.tester_runner import testSingleApi

router = Router()

APIS_PER_PAGE = 8
HEALTH_CONCURRENCY = 10
MD = ParseMode.MARKDOWN_V2


def isAdmin(userId: int) -> bool:
    return userId == ADMIN_ID


from bot.utils import PM, b, i, c, hEsc as _esc


class ApiAdminStates(StatesGroup):
    waitingApiJson     = State()
    waitingConfirm     = State()
    waitingEditJson    = State()
    waitingEditConfirm = State()
    waitingRename      = State()
    waitingTestPhone   = State()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def randomPhone() -> str:
    prefixes = ["98","97","96","95","94","93","91","90","89","88","87","86","85","84","83","82","81","80","79","78","77","76","75","74","73","72","70"]
    return random.choice(prefixes) + "".join(random.choices(string.digits, k=8))


def statusLabel(status: Optional[int], error: Optional[str], latencyMs: int) -> str:
    if error:
        return f"DEAD  {error[:35]}"
    if status is None:
        return "DEAD  no response"
    if status == 429:
        return f"RATE LIMITED  429  {latencyMs}ms"
    if status < 300:
        return f"OK  {status}  {latencyMs}ms"
    if status < 500:
        return f"CLIENT ERR  {status}  {latencyMs}ms"
    return f"SERVER ERR  {status}  {latencyMs}ms"


def getMergedTagged() -> List[dict]:
    from apis import API_CONFIGS as BASE
    customApis = db.getAllCustomApis()

    dbByUrl: dict = {}
    for row in customApis:
        cfg = json.loads(row["configJson"])
        dbByUrl[cfg.get("url", "")] = row

    result = []
    seenDbIds = set()

    for base in BASE:
        row = dbByUrl.get(base["url"])
        if row:
            cfg = json.loads(row["configJson"])
            cfg["_dbId"] = row["id"]
            cfg["_isOverride"] = True
            result.append(cfg)
            seenDbIds.add(row["id"])
        else:
            entry = dict(base)
            entry["_dbId"] = None
            entry["_isOverride"] = False
            result.append(entry)

    for row in customApis:
        if row["id"] not in seenDbIds:
            cfg = json.loads(row["configJson"])
            cfg["_dbId"] = row["id"]
            cfg["_isOverride"] = False
            result.append(cfg)

    return result


def cleanCfg(api: dict) -> dict:
    return {k: v for k, v in api.items() if not k.startswith("_")}


def formatDetail(cfg: dict) -> str:
    lines = ["API Detail\n"]
    lines.append(f"Name    : {cfg['name']}")
    lines.append(f"Method  : {cfg['method']}")
    lines.append(f"URL     : {cfg['url']}")
    if cfg.get("headers"):
        lines.append(f"Headers : {len(cfg['headers'])} fields")
    if cfg.get("json"):
        lines.append(f"Body    : JSON  {len(cfg['json'])} fields")
    elif cfg.get("data"):
        lines.append(f"Body    : Form  {len(cfg['data'])} fields")
    if cfg.get("params"):
        lines.append(f"Params  : {len(cfg['params'])} fields")
    if cfg.get("cookies"):
        lines.append(f"Cookies : {len(cfg['cookies'])} fields")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def apiManagerMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Add API",      callback_data="aapi:add")
    builder.button(text="List APIs",    callback_data="aapi:list:0")
    builder.button(text="Health Check", callback_data="aapi:health")
    builder.button(text="Back",         callback_data="adm:menu")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def apiListKeyboard(page: int, totalPages: int, pageApis: list, pageStart: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, api in enumerate(pageApis):
        dbId  = api.get("_dbId")
        label = api["name"]
        if api.get("_isOverride"):
            label += " [edited]"
        elif not dbId:
            label += " [base]"
        # callback: db id or global index — both guaranteed short
        cb = f"aapi:ddb:{dbId}" if dbId else f"aapi:didx:{pageStart + i}"
        builder.button(text=label, callback_data=cb)
    if page > 0:
        builder.button(text="Prev", callback_data=f"aapi:list:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"aapi:list:{page + 1}")
    builder.button(text="Back", callback_data="aapi:menu")
    builder.adjust(1)
    return builder.as_markup()


def apiDetailKeyboard(dbId: Optional[int], globalIdx: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    dbId      — set when API is in DB (custom or overridden base)
    globalIdx — set when API is a pure base API (not in DB yet)
    """
    builder = InlineKeyboardBuilder()
    if dbId:
        builder.button(text="Rename",    callback_data=f"aapi:rename:{dbId}")
        builder.button(text="Edit JSON", callback_data=f"aapi:edit:{dbId}")
        builder.button(text="Delete",    callback_data=f"aapi:delete:{dbId}")
        builder.button(text="Test",      callback_data=f"aapi:testone:{dbId}")
    else:
        # Base API not yet in DB — use global index to copy it
        builder.button(text="Edit (copy to bot)", callback_data=f"aapi:copyidx:{globalIdx}")
        builder.button(text="Test",               callback_data=f"aapi:testoneidx:{globalIdx}")
    builder.button(text="Back", callback_data="aapi:list:0")
    builder.adjust(2, 1, 1) if dbId else builder.adjust(1, 1, 1)
    return builder.as_markup()


def confirmKeyboard(confirmCb: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Save",   callback_data=confirmCb)
    builder.button(text="Cancel", callback_data="aapi:menu")
    builder.adjust(2)
    return builder.as_markup()


def backToApiMenuKeyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="API Manager", callback_data="aapi:menu")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:menu")
async def cbApiMenu(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.clear()
    allApis   = getMergedTagged()
    custom    = sum(1 for a in allApis if a.get("_dbId") and not a.get("_isOverride"))
    overrides = sum(1 for a in allApis if a.get("_isOverride"))
    base      = len(allApis) - custom - overrides
    await callback.message.edit_text(
        f"API Manager\n\n"
        f"Total : {len(allApis)}\n"
        f"Base  : {base}  |  Edited : {overrides}  |  Custom : {custom}",
        reply_markup=apiManagerMenuKeyboard()
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:list:"))
async def cbListApis(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    page       = int(callback.data.split(":")[2])
    allApis    = getMergedTagged()
    total      = len(allApis)
    totalPages = max(1, -(-total // APIS_PER_PAGE))
    start      = page * APIS_PER_PAGE
    pageApis   = allApis[start:start + APIS_PER_PAGE]

    lines = [f"APIs  ({total} total  |  page {page + 1}/{totalPages})\n"]
    for i, api in enumerate(pageApis, start=start + 1):
        tag = " [edited]" if api.get("_isOverride") else (" [base]" if not api.get("_dbId") else " [custom]")
        url = api["url"][:48] + "..." if len(api["url"]) > 48 else api["url"]
        lines.append(f"{i}. {api['name']}  {api['method']}{tag}\n   {url}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=apiListKeyboard(page, totalPages, pageApis, start)
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Detail — by DB id
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:ddb:"))
async def cbDetailDb(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("API not found.", show_alert=True)
        return

    cfg = json.loads(row["configJson"])
    await callback.message.edit_text(
        formatDetail(cfg),
        reply_markup=apiDetailKeyboard(dbId=dbId)
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Detail — by global index (base APIs)
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:didx:"))
async def cbDetailIdx(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    idx     = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    if idx >= len(allApis):
        await callback.answer("API not found.", show_alert=True)
        return

    api = allApis[idx]
    await callback.message.edit_text(
        formatDetail(api),
        reply_markup=apiDetailKeyboard(dbId=None, globalIdx=idx)
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Copy base API to DB for editing
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:copyidx:"))
async def cbCopyBase(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    idx     = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    if idx >= len(allApis):
        await callback.answer("API not found.", show_alert=True)
        return

    api = allApis[idx]
    if api.get("_dbId"):
        await callback.answer("Already in bot DB.", show_alert=True)
        return

    cfg     = cleanCfg(api)
    cfgJson = json.dumps(cfg)
    dbId    = db.addCustomApi(name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=cfgJson)

    await state.set_state(ApiAdminStates.waitingEditJson)
    await state.update_data(editApiId=dbId)
    await callback.message.edit_text(
        f"Copied to bot. Paste updated JSON to edit {cfg['name']}.\n\n"
        f"Current:\n{json.dumps(cfg, indent=2)}"
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:rename:"))
async def cbRename(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("Not found.", show_alert=True)
        return

    await state.set_state(ApiAdminStates.waitingRename)
    await state.update_data(renameApiId=dbId)
    await callback.message.edit_text(
        f"Rename: {row['name']}\n\nType the new name."
    )
    await callback.answer()


@router.message(StateFilter(ApiAdminStates.waitingRename))
async def handleRename(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return

    newName = (message.text or "").strip()
    if not newName or len(newName) > 64:
        await message.answer("Name must be 1–64 characters.")
        return

    data = await state.get_data()
    dbId = data["renameApiId"]
    row  = db.getCustomApi(dbId)
    if not row:
        await message.answer("API no longer exists.")
        await state.clear()
        return

    cfg         = json.loads(row["configJson"])
    cfg["name"] = newName
    db.updateCustomApi(dbId, name=newName, method=cfg["method"], url=cfg["url"], configJson=json.dumps(cfg))
    await state.clear()
    await message.answer(f"Renamed to: {newName}", reply_markup=apiDetailKeyboard(dbId=dbId))


# ---------------------------------------------------------------------------
# Edit JSON
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:edit:"))
async def cbEditApi(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("Not found.", show_alert=True)
        return

    await state.set_state(ApiAdminStates.waitingEditJson)
    await state.update_data(editApiId=dbId)

    cfg = json.loads(row["configJson"])
    await callback.message.edit_text(
        f"Edit: {cfg['name']}\n\nPaste updated JSON.\n\nCurrent:\n{json.dumps(cfg, indent=2)}"
    )
    await callback.answer()


@router.message(StateFilter(ApiAdminStates.waitingEditJson))
async def handleEditJson(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return

    raw      = (message.text or "").strip()
    ok, cfg, error = apiManager.validateApiJson(raw)
    if not ok:
        await message.answer(f"Invalid JSON.\n\n{error}\n\nFix and paste again or /start to cancel.")
        return

    data = await state.get_data()
    dbId = data.get("editApiId")
    await state.update_data(editApiJson=json.dumps(cfg), editApiConfig=cfg)
    await state.set_state(ApiAdminStates.waitingEditConfirm)

    builder = InlineKeyboardBuilder()
    builder.button(text="Save",   callback_data="aapi:confirm_edit")
    builder.button(text="Cancel", callback_data=f"aapi:ddb:{dbId}")
    builder.adjust(2)
    await message.answer(f"{formatDetail(cfg)}\n\nSave?", reply_markup=builder.as_markup())


@router.callback_query(F.data == "aapi:confirm_edit", StateFilter(ApiAdminStates.waitingEditConfirm))
async def cbConfirmEdit(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    data    = await state.get_data()
    dbId    = data.get("editApiId")
    cfg     = data.get("editApiConfig")
    cfgJson = data.get("editApiJson")

    if not all([dbId, cfg, cfgJson]):
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return

    db.updateCustomApi(dbId, name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=cfgJson)
    await state.clear()
    await callback.message.edit_text(
        f"Saved.  {cfg['name']} ({cfg['method']}) updated.",
        reply_markup=apiDetailKeyboard(dbId=dbId)
    )
    await callback.answer("Saved.")


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:delete:"))
async def cbDeleteApi(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    dbId = int(callback.data.split(":")[2])
    row  = db.getCustomApi(dbId)
    if not row:
        await callback.answer("Not found.", show_alert=True)
        return

    db.deleteCustomApi(dbId)
    await callback.answer(f"Deleted: {row['name']}")
    await callback.message.edit_text("Deleted.", reply_markup=backToApiMenuKeyboard())


# ---------------------------------------------------------------------------
# Add new API
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:add")
async def cbAddApi(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return
    await state.set_state(ApiAdminStates.waitingApiJson)
    await callback.message.edit_text(
        "Add API\n\n"
        "Paste full JSON config.\n"
        "Required: name, method, url\n"
        "Optional: headers, json, data, params, cookies\n\n"
        'Example:\n{"name": "MyApp", "method": "POST", "url": "https://api.example.com/otp", '
        '"headers": {"content-type": "application/json"}, "json": {"phone": "{phone}"}}'
    )
    await callback.answer()


@router.message(StateFilter(ApiAdminStates.waitingApiJson))
async def handleApiJson(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return

    raw      = (message.text or "").strip()
    ok, cfg, error = apiManager.validateApiJson(raw)
    if not ok:
        await message.answer(f"Invalid.\n\n{error}\n\nFix and paste again.")
        return

    await state.update_data(pendingApiJson=json.dumps(cfg), pendingApiConfig=cfg)
    await state.set_state(ApiAdminStates.waitingConfirm)
    await message.answer(f"{formatDetail(cfg)}\n\nSave?", reply_markup=confirmKeyboard("aapi:confirm_save"))


@router.callback_query(F.data == "aapi:confirm_save", StateFilter(ApiAdminStates.waitingConfirm))
async def cbConfirmSave(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    data    = await state.get_data()
    cfg     = data.get("pendingApiConfig")
    cfgJson = data.get("pendingApiJson")
    if not cfg or not cfgJson:
        await callback.answer("Session expired.", show_alert=True)
        await state.clear()
        return

    dbId  = db.addCustomApi(name=cfg["name"], method=cfg["method"], url=cfg["url"], configJson=cfgJson)
    await state.clear()
    total = len(getMergedTagged())
    await callback.message.edit_text(
        f"Saved.  {cfg['name']} added.  Total APIs: {total}",
        reply_markup=backToApiMenuKeyboard()
    )
    await callback.answer("Saved.")


# ---------------------------------------------------------------------------
# Test single API
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("aapi:testone:"))
async def cbTestOne(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    dbId    = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    api     = next((a for a in allApis if a.get("_dbId") == dbId), None)
    if not api:
        await callback.answer("API not found.", show_alert=True)
        return

    await state.set_state(ApiAdminStates.waitingTestPhone)
    await state.update_data(testApiDbId=dbId, testApiIdx=None)
    await callback.message.edit_text(
        f"Test: {api['name']}\n{api['method']}  {api['url']}\n\nEnter a 10-digit phone number."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aapi:testoneidx:"))
async def cbTestOneIdx(callback: CallbackQuery, state: FSMContext) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    idx     = int(callback.data.split(":")[2])
    allApis = getMergedTagged()
    if idx >= len(allApis):
        await callback.answer("API not found.", show_alert=True)
        return

    api = allApis[idx]
    await state.set_state(ApiAdminStates.waitingTestPhone)
    await state.update_data(testApiDbId=None, testApiIdx=idx)
    await callback.message.edit_text(
        f"Test: {api['name']}\n{api['method']}  {api['url']}\n\nEnter a 10-digit phone number."
    )
    await callback.answer()


@router.message(StateFilter(ApiAdminStates.waitingTestPhone))
async def handleTestPhone(message: Message, state: FSMContext) -> None:
    if not isAdmin(message.from_user.id):
        return

    phone = (message.text or "").strip()
    if not phone.isdigit() or len(phone) != 10:
        await message.answer("Enter exactly 10 digits.")
        return

    data    = await state.get_data()
    dbId    = data.get("testApiDbId")
    idx     = data.get("testApiIdx")
    allApis = getMergedTagged()

    if dbId is not None:
        api = next((a for a in allApis if a.get("_dbId") == dbId), None)
    elif idx is not None:
        api = allApis[idx] if idx < len(allApis) else None
    else:
        api = None

    if not api:
        await message.answer("API no longer available.")
        await state.clear()
        return

    await state.clear()
    cfg     = cleanCfg(api)
    waiting = await message.answer(f"Testing {api['name']}...")
    result  = await testSingleApi(cfg, phone)

    if not result["ok"]:
        await waiting.edit_text(
            f"Test Failed\n\nAPI   : {api['name']}\nError : {result['error']}",
            reply_markup=backToApiMenuKeyboard()
        )
        return

    status  = result["status"]
    latency = result["latencyMs"]
    snippet = (result.get("snippet") or "(empty)")[:100]
    label   = statusLabel(status, None, latency)

    await waiting.edit_text(
        f"Test Result\n\n"
        f"API      : {api['name']}\n"
        f"Result   : {label}\n"
        f"Response : {snippet}",
        reply_markup=backToApiMenuKeyboard()
    )


# ---------------------------------------------------------------------------
# Health Check — test ALL APIs simultaneously with a random number
# ---------------------------------------------------------------------------

@router.callback_query(F.data == "aapi:health")
async def cbHealthCheck(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    allApis = getMergedTagged()
    if not allApis:
        await callback.answer("No APIs loaded.", show_alert=True)
        return

    phone   = randomPhone()
    total   = len(allApis)
    waiting = await callback.message.edit_text(
        f"*Health Check*\n\n`Testing {total} APIs...`\n_Please wait\\._",
        parse_mode=PM
    )
    await callback.answer()

    semaphore = asyncio.Semaphore(HEALTH_CONCURRENCY)

    async def checkOne(api: dict) -> dict:
        async with semaphore:
            cfg    = cleanCfg(api)
            result = await testSingleApi(cfg, phone)
            return {"name": api["name"], "method": api["method"], "result": result}

    results = await asyncio.gather(*[checkOne(a) for a in allApis])

    # Categorize
    ok_list   = []
    rl_list   = []
    dead_list = []
    err_list  = []

    for r in results:
        res    = r["result"]
        status = res.get("status")
        if not res["ok"] or status is None:
            dead_list.append(r)
        elif status == 429:
            rl_list.append(r)
        elif status < 300:
            ok_list.append(r)
        else:
            err_list.append(r)

    # Store results in state for browsing
    import json as _json
    storageKey = f"hc_{callback.from_user.id}"
    _healthCheckCache[storageKey] = {
        "phone": phone,
        "ok":    ok_list,
        "rl":    rl_list,
        "dead":  dead_list,
        "err":   err_list,
    }

    builder = InlineKeyboardBuilder()
    if ok_list:
        builder.button(text=f"OK  ({len(ok_list)})",           callback_data=f"aapi:hccat:ok:0")
    if dead_list:
        builder.button(text=f"Dead  ({len(dead_list)})",       callback_data=f"aapi:hccat:dead:0")
    if rl_list:
        builder.button(text=f"Rate Limited  ({len(rl_list)})", callback_data=f"aapi:hccat:rl:0")
    if err_list:
        builder.button(text=f"Errors  ({len(err_list)})",      callback_data=f"aapi:hccat:err:0")
    builder.button(text="Run Again", callback_data="aapi:health")
    builder.button(text="Back",      callback_data="aapi:menu")
    builder.adjust(1)

    total = len(ok_list) + len(dead_list) + len(rl_list) + len(err_list)
    await waiting.edit_text(
        f"*Health Check*\n"
        f"`{total}` APIs tested  ·  `{phone}`\n\n"
        f"OK              `{len(ok_list)}`\n"
        f"Dead            `{len(dead_list)}`\n"
        f"Rate limited    `{len(rl_list)}`\n"
        f"Errors          `{len(err_list)}`\n\n"
        f"_Tap a category to browse\\._",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )


# In-memory cache for health check results (keyed by user id)
_healthCheckCache: dict = {}

HC_PER_PAGE = 8


@router.callback_query(F.data.startswith("aapi:hccat:"))
async def cbHcCategory(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    parts    = callback.data.split(":")
    cat      = parts[2]
    page     = int(parts[3])
    cacheKey = f"hc_{callback.from_user.id}"
    cache    = _healthCheckCache.get(cacheKey)

    if not cache:
        await callback.answer("Results expired. Run health check again.", show_alert=True)
        return

    catMap   = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}
    catLabel = {"ok": "OK", "dead": "Dead", "rl": "Rate Limited", "err": "Errors"}
    entries  = catMap.get(cat, [])
    total    = len(entries)
    totalPages = max(1, -(-total // HC_PER_PAGE))
    start    = page * HC_PER_PAGE
    pageEntries = entries[start:start + HC_PER_PAGE]

    builder = InlineKeyboardBuilder()
    for i, r in enumerate(pageEntries):
        globalIdx = start + i
        builder.button(
            text=f"{r['name']} ({r['method']})",
            callback_data=f"aapi:hcresult:{cat}:{globalIdx}"
        )
    if page > 0:
        builder.button(text="Prev", callback_data=f"aapi:hccat:{cat}:{page - 1}")
    if page < totalPages - 1:
        builder.button(text="Next", callback_data=f"aapi:hccat:{cat}:{page + 1}")
    builder.button(text="Back", callback_data="aapi:health_summary")
    builder.adjust(1)

    await callback.message.edit_text(
        f"*{catLabel[cat]} APIs*  `{total} total`\n\n_Tap an API to see its result\\._",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aapi:hcresult:"))
async def cbHcResult(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    parts    = callback.data.split(":")
    cat      = parts[2]
    idx      = int(parts[3])
    cacheKey = f"hc_{callback.from_user.id}"
    cache    = _healthCheckCache.get(cacheKey)

    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return

    catMap  = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}
    entries = catMap.get(cat, [])

    if idx >= len(entries):
        await callback.answer("Not found.", show_alert=True)
        return

    r      = entries[idx]
    res    = r["result"]
    name   = r["name"]
    method = r["method"]
    page   = idx // HC_PER_PAGE

    # Check current skip status
    isSkipped = db.isApiSkipped(name)

    if not res["ok"] or res.get("status") is None:
        err  = _esc((res.get("error") or "timeout")[:80])
        text = (
            f"*{_esc(name)}*  `{method}`\n\n"
            f"Status   `DEAD`\n"
            f"Error    `{err}`"
        )
    else:
        status  = res["status"]
        latency = res.get("latencyMs", 0)
        snippet = _esc((res.get("snippet") or "(empty)")[:100])
        if status == 429:
            lbl = "RATE LIMITED"
        elif status < 300:
            lbl = "OK"
        elif status < 500:
            lbl = "CLIENT ERR"
        else:
            lbl = "SERVER ERR"
        text = (
            f"*{_esc(name)}*  `{method}`\n\n"
            f"Status   `{lbl} {status}`\n"
            f"Latency  `{latency}ms`\n\n"
            f"_Response_\n`{snippet}`"
        )

    skipLabel = "Enable" if isSkipped else "Skip next time"
    # Encode name safely for callback — use idx instead of name to avoid length issues
    builder = InlineKeyboardBuilder()
    if cat in ("dead", "err"):
        builder.button(text=skipLabel,      callback_data=f"aapi:hcskip:{cat}:{idx}")
        builder.button(text="Delete API",   callback_data=f"aapi:hcdelete:{cat}:{idx}")
        builder.button(text="Back",         callback_data=f"aapi:hccat:{cat}:{page}")
        builder.adjust(2, 1)
    else:
        builder.button(text=skipLabel,      callback_data=f"aapi:hcskip:{cat}:{idx}")
        builder.button(text="Back",         callback_data=f"aapi:hccat:{cat}:{page}")
        builder.adjust(1)

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode=PM)
    await callback.answer()


@router.callback_query(F.data.startswith("aapi:hcskip:"))
async def cbHcSkip(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    parts    = callback.data.split(":")
    cat      = parts[2]
    idx      = int(parts[3])
    cacheKey = f"hc_{callback.from_user.id}"
    cache    = _healthCheckCache.get(cacheKey)

    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return

    catMap  = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}
    entries = catMap.get(cat, [])
    if idx >= len(entries):
        await callback.answer("Not found.", show_alert=True)
        return

    name      = entries[idx]["name"]
    isSkipped = db.isApiSkipped(name)

    if isSkipped:
        db.unskipApi(name)
        await callback.answer(f"Enabled: {name}")
    else:
        db.skipApi(name)
        await callback.answer(f"Will skip: {name}")

    # Refresh result screen
    await cbHcResult(callback)


@router.callback_query(F.data.startswith("aapi:hcdelete:"))
async def cbHcDelete(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    parts    = callback.data.split(":")
    cat      = parts[2]
    idx      = int(parts[3])
    cacheKey = f"hc_{callback.from_user.id}"
    cache    = _healthCheckCache.get(cacheKey)

    if not cache:
        await callback.answer("Results expired.", show_alert=True)
        return

    catMap  = {"ok": cache["ok"], "dead": cache["dead"], "rl": cache["rl"], "err": cache["err"]}
    entries = catMap.get(cat, [])
    if idx >= len(entries):
        await callback.answer("Not found.", show_alert=True)
        return

    name = entries[idx]["name"]

    # Find the DB id for this API by name
    allApis = getMergedTagged()
    api     = next((a for a in allApis if a["name"] == name and a.get("_dbId")), None)

    if not api:
        await callback.answer("Base APIs cannot be deleted — use Skip instead.", show_alert=True)
        return

    db.deleteCustomApi(api["_dbId"])
    # Remove from cache so list refreshes cleanly
    entries.pop(idx)
    await callback.answer(f"Deleted: {name}")

    page = idx // HC_PER_PAGE
    await callback.message.edit_text(
        f"*Deleted*  `{_esc(name)}`\n\n_API removed from the bot\\._",
        reply_markup=InlineKeyboardBuilder().button(
            text="Back", callback_data=f"aapi:hccat:{cat}:{page}"
        ).as_markup(),
        parse_mode=PM
    )


@router.callback_query(F.data == "aapi:health_summary")
async def cbHealthSummary(callback: CallbackQuery) -> None:
    if not isAdmin(callback.from_user.id):
        await callback.answer("Access denied.", show_alert=True)
        return

    cacheKey = f"hc_{callback.from_user.id}"
    cache    = _healthCheckCache.get(cacheKey)

    if not cache:
        await callback.answer("Results expired. Run health check again.", show_alert=True)
        return

    ok_list   = cache["ok"]
    dead_list = cache["dead"]
    rl_list   = cache["rl"]
    err_list  = cache["err"]
    phone     = cache["phone"]

    builder = InlineKeyboardBuilder()
    if ok_list:
        builder.button(text=f"OK  ({len(ok_list)})",           callback_data="aapi:hccat:ok:0")
    if dead_list:
        builder.button(text=f"Dead  ({len(dead_list)})",       callback_data="aapi:hccat:dead:0")
    if rl_list:
        builder.button(text=f"Rate Limited  ({len(rl_list)})", callback_data="aapi:hccat:rl:0")
    if err_list:
        builder.button(text=f"Errors  ({len(err_list)})",      callback_data="aapi:hccat:err:0")
    builder.button(text="Run Again", callback_data="aapi:health")
    builder.button(text="Back",      callback_data="aapi:menu")
    builder.adjust(2, 2, 2)

    await callback.message.edit_text(
        f"*Health Check*\n"
        f"`Phone: {phone}`\n\n"
        f"OK            `{len(ok_list)}`\n"
        f"Dead          `{len(dead_list)}`\n"
        f"Rate limited  `{len(rl_list)}`\n"
        f"Errors        `{len(err_list)}`\n\n"
        f"_Tap a category to browse results\\._",
        reply_markup=builder.as_markup(),
        parse_mode=PM
    )
    await callback.answer()