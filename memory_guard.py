from __future__ import annotations

"""
Memory guard — runs in background, monitors RAM every 30 seconds.
If usage crosses threshold it stops all active tests gracefully
to prevent Railway OOM kill.

Free tier Railway = ~512MB RAM
We stop tests at 400MB to give headroom.
"""

import asyncio
import gc
import logging
import os

logger = logging.getLogger(__name__)

# Stop all tests if RAM exceeds this (MB)
# Railway free = 512MB, we stop at 380MB for safety
MEMORY_WARN_MB  = 300
MEMORY_LIMIT_MB = 380
CHECK_INTERVAL  = 30  # seconds


def getMemoryMb() -> float:
    """Get current process memory in MB."""
    try:
        # Read from /proc/self/status — works on Linux (Railway)
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb / 1024
    except Exception:
        pass
    # Fallback using resource module
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return kb / 1024
    except Exception:
        pass
    return 0.0


async def memoryGuardLoop(bot=None, adminId: int = 0) -> None:
    """
    Background loop that monitors memory and stops tests if needed.
    """
    logger.info("Memory guard started.")

    while True:
        await asyncio.sleep(CHECK_INTERVAL)

        memMb = getMemoryMb()

        if memMb <= 0:
            continue

        logger.info(f"Memory usage: {memMb:.1f} MB")

        if memMb >= MEMORY_LIMIT_MB:
            logger.warning(f"Memory critical: {memMb:.1f}MB — stopping all active tests")

            # Stop all active tests
            try:
                from bot.handlers.test_flow import activeRunners
                runnersCopy = dict(activeRunners)
                for userId, runner in runnersCopy.items():
                    try:
                        await runner.stop()
                        logger.info(f"Stopped test for user {userId} due to OOM prevention")
                    except Exception as e:
                        logger.error(f"Error stopping runner for {userId}: {e}")
            except Exception as e:
                logger.error(f"Error accessing activeRunners: {e}")

            # Force garbage collection
            gc.collect()

            memAfter = getMemoryMb()
            logger.info(f"Memory after cleanup: {memAfter:.1f} MB")

            # Notify admin
            if bot and adminId:
                try:
                    await bot.send_message(
                        adminId,
                        f"⚠️ <b>Memory Warning</b>\n\n"
                        f"RAM usage reached <code>{memMb:.0f}MB</code>\n"
                        f"All active tests stopped automatically.\n"
                        f"Memory after cleanup: <code>{memAfter:.0f}MB</code>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

        elif memMb >= MEMORY_WARN_MB:
            logger.warning(f"Memory high: {memMb:.1f}MB — running gc")
            gc.collect()
            memAfter = getMemoryMb()
            logger.info(f"Memory after gc: {memAfter:.1f} MB")
