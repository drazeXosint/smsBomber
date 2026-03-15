import sys
import os
import traceback

# MUST be set before any bot imports so helpers/apis are findable
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import asyncio

if __name__ == "__main__":
    try:
        from bot.config import DB_FILE
        print(f"[DB] Using database: {DB_FILE}")
        print(f"[DB] Exists: {os.path.exists(DB_FILE)}")

        from bot.main import main
        asyncio.run(main())
    except Exception as e:
        print(f"\n{'='*60}")
        print(f"STARTUP ERROR: {e}")
        print('='*60)
        traceback.print_exc()
        input("\nPress Enter to exit...")