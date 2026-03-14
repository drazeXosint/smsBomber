from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from bot.config import DB_FILE, DEFAULT_DAILY_LIMIT, IST_OFFSET_HOURS

IST = timezone(timedelta(hours=IST_OFFSET_HOURS))


def getIstToday() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def getSecondsUntilMidnightIst() -> float:
    now = datetime.now(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - now).total_seconds()


class Database:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._createTables()

    def _createTables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                userId        INTEGER PRIMARY KEY,
                username      TEXT,
                firstName     TEXT,
                lastName      TEXT,
                joinedAt      REAL NOT NULL,
                isBanned      INTEGER NOT NULL DEFAULT 0,
                dailyLimit    INTEGER NOT NULL DEFAULT 10,
                testsToday    INTEGER NOT NULL DEFAULT 0,
                lastResetDate TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS testHistory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                userId      INTEGER NOT NULL,
                phone       TEXT NOT NULL,
                duration    INTEGER NOT NULL,
                workers     INTEGER NOT NULL,
                totalReqs   INTEGER NOT NULL DEFAULT 0,
                otpHits     INTEGER NOT NULL DEFAULT 0,
                errors      INTEGER NOT NULL DEFAULT 0,
                rps         REAL NOT NULL DEFAULT 0,
                startedAt   REAL NOT NULL,
                finishedAt  REAL,
                FOREIGN KEY (userId) REFERENCES users(userId)
            );

            CREATE TABLE IF NOT EXISTS customApis (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                method      TEXT NOT NULL,
                url         TEXT NOT NULL,
                configJson  TEXT NOT NULL,
                addedAt     REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proxyFiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT NOT NULL,
                content     TEXT NOT NULL,
                proxyCount  INTEGER NOT NULL DEFAULT 0,
                uploadedAt  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS blacklistedPhones (
                phone       TEXT PRIMARY KEY,
                reason      TEXT NOT NULL DEFAULT '',
                addedAt     REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skippedApis (
                name        TEXT PRIMARY KEY,
                addedAt     REAL NOT NULL
            );
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def registerUser(self, userId: int, username: Optional[str], firstName: str, lastName: Optional[str]) -> bool:
        existing = self._conn.execute(
            "SELECT userId FROM users WHERE userId = ?", (userId,)
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE users SET username=?, firstName=?, lastName=? WHERE userId=?",
                (username, firstName, lastName or "", userId)
            )
            self._conn.commit()
            return False
        self._conn.execute(
            "INSERT INTO users (userId, username, firstName, lastName, joinedAt, dailyLimit, lastResetDate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (userId, username, firstName, lastName or "", time.time(), DEFAULT_DAILY_LIMIT, getIstToday())
        )
        self._conn.commit()
        return True

    def getUser(self, userId: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute("SELECT * FROM users WHERE userId = ?", (userId,)).fetchone()
        return dict(row) if row else None

    def getAllUsers(self, offset: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM users ORDER BY joinedAt DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

    def getUserCount(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def banUser(self, userId: int) -> None:
        self._conn.execute("UPDATE users SET isBanned=1 WHERE userId=?", (userId,))
        self._conn.commit()

    def unbanUser(self, userId: int) -> None:
        self._conn.execute("UPDATE users SET isBanned=0 WHERE userId=?", (userId,))
        self._conn.commit()

    def setDailyLimit(self, userId: int, limit: int) -> None:
        self._conn.execute("UPDATE users SET dailyLimit=? WHERE userId=?", (limit, userId))
        self._conn.commit()

    def setGlobalDailyLimit(self, limit: int) -> None:
        self._conn.execute("UPDATE users SET dailyLimit=?", (limit,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Daily limit logic
    # ------------------------------------------------------------------

    def _ensureResetForUser(self, userId: int) -> None:
        today = getIstToday()
        row = self._conn.execute(
            "SELECT lastResetDate FROM users WHERE userId=?", (userId,)
        ).fetchone()
        if row and row["lastResetDate"] != today:
            self._conn.execute(
                "UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?",
                (today, userId)
            )
            self._conn.commit()

    def canRunTest(self, userId: int) -> tuple:
        self._ensureResetForUser(userId)
        row = self._conn.execute(
            "SELECT testsToday, dailyLimit, isBanned FROM users WHERE userId=?", (userId,)
        ).fetchone()
        if not row:
            return False, 0, 0
        if row["isBanned"]:
            return False, row["testsToday"], row["dailyLimit"]
        allowed = row["testsToday"] < row["dailyLimit"]
        return allowed, row["testsToday"], row["dailyLimit"]

    def incrementTestCount(self, userId: int) -> None:
        self._ensureResetForUser(userId)
        self._conn.execute(
            "UPDATE users SET testsToday = testsToday + 1 WHERE userId=?", (userId,)
        )
        self._conn.commit()

    def resetUserTests(self, userId: int) -> None:
        self._conn.execute(
            "UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?",
            (getIstToday(), userId)
        )
        self._conn.commit()

    def resetAllTests(self) -> None:
        today = getIstToday()
        self._conn.execute("UPDATE users SET testsToday=0, lastResetDate=?", (today,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Test history
    # ------------------------------------------------------------------

    def startTestRecord(self, userId: int, phone: str, duration: int, workers: int) -> int:
        cur = self._conn.execute(
            "INSERT INTO testHistory (userId, phone, duration, workers, startedAt) VALUES (?,?,?,?,?)",
            (userId, phone, duration, workers, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def finishTestRecord(self, recordId: int, totalReqs: int, otpHits: int, errors: int, rps: float) -> None:
        self._conn.execute(
            "UPDATE testHistory SET totalReqs=?, otpHits=?, errors=?, rps=?, finishedAt=? WHERE id=?",
            (totalReqs, otpHits, errors, rps, time.time(), recordId)
        )
        self._conn.commit()

    def getUserHistory(self, userId: int, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM testHistory WHERE userId=? ORDER BY startedAt DESC LIMIT ?",
            (userId, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Custom APIs
    # ------------------------------------------------------------------

    def addCustomApi(self, name: str, method: str, url: str, configJson: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO customApis (name, method, url, configJson, addedAt) VALUES (?,?,?,?,?)",
            (name, method, url, configJson, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def getAllCustomApis(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM customApis ORDER BY addedAt DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def updateCustomApi(self, apiId: int, name: str, method: str, url: str, configJson: str) -> None:
        self._conn.execute(
            "UPDATE customApis SET name=?, method=?, url=?, configJson=? WHERE id=?",
            (name, method, url, configJson, apiId)
        )
        self._conn.commit()

    def deleteCustomApi(self, apiId: int) -> None:
        self._conn.execute("DELETE FROM customApis WHERE id=?", (apiId,))
        self._conn.commit()

    def getCustomApi(self, apiId: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM customApis WHERE id=?", (apiId,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Proxy files
    # ------------------------------------------------------------------

    def addProxyFile(self, label: str, content: str, proxyCount: int) -> int:
        cur = self._conn.execute(
            "INSERT INTO proxyFiles (label, content, proxyCount, uploadedAt) VALUES (?,?,?,?)",
            (label, content, proxyCount, time.time())
        )
        self._conn.commit()
        return cur.lastrowid

    def getAllProxyFiles(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM proxyFiles ORDER BY uploadedAt DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def getProxyFile(self, fileId: int) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT * FROM proxyFiles WHERE id=?", (fileId,)
        ).fetchone()
        return dict(row) if row else None

    def deleteProxyFile(self, fileId: int) -> None:
        self._conn.execute("DELETE FROM proxyFiles WHERE id=?", (fileId,))
        self._conn.commit()

    def getAllProxies(self) -> List[str]:
        rows = self._conn.execute("SELECT content FROM proxyFiles").fetchall()
        proxies = []
        for row in rows:
            for line in row["content"].splitlines():
                line = line.strip()
                if line:
                    proxies.append(line)
        return proxies

    # ------------------------------------------------------------------
    # Phone blacklist
    # ------------------------------------------------------------------

    def blacklistPhone(self, phone: str, reason: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO blacklistedPhones (phone, reason, addedAt) VALUES (?,?,?)",
            (phone, reason, time.time())
        )
        self._conn.commit()

    def unblacklistPhone(self, phone: str) -> None:
        self._conn.execute("DELETE FROM blacklistedPhones WHERE phone=?", (phone,))
        self._conn.commit()

    def isPhoneBlacklisted(self, phone: str) -> bool:
        row = self._conn.execute(
            "SELECT phone FROM blacklistedPhones WHERE phone=?", (phone,)
        ).fetchone()
        return row is not None

    def getAllBlacklisted(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM blacklistedPhones ORDER BY addedAt DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Skipped APIs
    # ------------------------------------------------------------------

    def skipApi(self, name: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO skippedApis (name, addedAt) VALUES (?,?)",
            (name, time.time())
        )
        self._conn.commit()

    def unskipApi(self, name: str) -> None:
        self._conn.execute("DELETE FROM skippedApis WHERE name=?", (name,))
        self._conn.commit()

    def getSkippedApiNames(self) -> set:
        rows = self._conn.execute("SELECT name FROM skippedApis").fetchall()
        return {r["name"] for r in rows}

    def isApiSkipped(self, name: str) -> bool:
        row = self._conn.execute("SELECT name FROM skippedApis WHERE name=?", (name,)).fetchone()
        return row is not None

    def close(self) -> None:
        self._conn.close()


db = Database()