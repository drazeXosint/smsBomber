import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.config import BOT_TOKEN
from bot.handlers import start, test_flow, dashboard, admin
from bot.handlers import admin_apis, admin_proxy, user_features, admin_features, schedule_handler, live_dashboard, nuke_handler, distributed_handler
from bot.middleware.auth import AuthMiddleware
from bot.services.scheduler import midnightResetLoop, scheduledTestsLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "")
PORT         = int(os.getenv("PORT", 8080))
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL  = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
USE_WEBHOOK  = bool(WEBHOOK_HOST)


def registerRouters(dp: Dispatcher) -> None:
    dp.update.middleware(AuthMiddleware())
    dp.include_router(start.router)
    dp.include_router(test_flow.router)
    dp.include_router(dashboard.router)
    dp.include_router(admin.router)
    dp.include_router(admin_apis.router)
    dp.include_router(admin_proxy.router)
    dp.include_router(user_features.router)
    dp.include_router(admin_features.router)
    dp.include_router(schedule_handler.router)
    dp.include_router(live_dashboard.router)
    dp.include_router(nuke_handler.router)
    dp.include_router(distributed_handler.router)


async def onStartup(bot: Bot) -> None:
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set: {WEBHOOK_URL}")


async def onShutdown(bot: Bot) -> None:
    await bot.delete_webhook()
    logger.info("Webhook deleted.")


async def mainWebhook() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    registerRouters(dp)
    dp.startup.register(onStartup)
    dp.shutdown.register(onShutdown)

    app = web.Application()
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    asyncio.create_task(midnightResetLoop())
    asyncio.create_task(scheduledTestsLoop(bot))

    # Memory guard — prevents OOM on Railway free tier
    try:
        from memory_guard import memoryGuardLoop
        from bot.config import ADMIN_ID
        asyncio.create_task(memoryGuardLoop(bot=bot, adminId=ADMIN_ID))
    except Exception:
        pass

    # Start distributed coordination
    try:
        from distributed import startDistributed
        from bot.services.database import db
        asyncio.create_task(startDistributed(db, bot))
    except Exception:
        pass

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info(f"Bot running on webhook — port {PORT}")
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


async def mainPolling() -> None:
    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    registerRouters(dp)
    logger.info("Bot starting in polling mode...")
    try:
        asyncio.create_task(midnightResetLoop())
        asyncio.create_task(scheduledTestsLoop(bot))
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


async def main() -> None:
    if USE_WEBHOOK:
        logger.info("Webhook mode enabled.")
        await mainWebhook()
    else:
        logger.info("No WEBHOOK_HOST set — falling back to polling mode.")
        await mainPolling()


if __name__ == "__main__":
    asyncio.run(main())
