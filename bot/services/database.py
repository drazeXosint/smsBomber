from __future__ import annotations

import os
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import libsql_experimental as libsql
from dotenv import load_dotenv

from bot.config import DEFAULT_DAILY_LIMIT, IST_OFFSET_HOURS

load_dotenv()

IST = timezone(timedelta(hours=IST_OFFSET_HOURS))

TURSO_URL   = os.getenv("TURSO_URL", "")
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "")

if not TURSO_URL or not TURSO_TOKEN:
    raise RuntimeError("TURSO_URL and TURSO_TOKEN must be set in your .env file.")

LOCAL_DB_PATH = "/app/local_replica.db"

# Sync to Turso every N writes — reduces network calls massively
SYNC_EVERY_N_WRITES = 10


def getIstToday() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def getSecondsUntilMidnightIst() -> float:
    now      = datetime.now(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - now).total_seconds()


class Database:
    def __init__(self) -> None:
        self._lock       = threading.Lock()
        self._writeCount = 0
        self._conn = libsql.connect(
            database=LOCAL_DB_PATH,
            sync_url=TURSO_URL,
            auth_token=TURSO_TOKEN,
        )
        self._conn.sync()
        self._createTables()

    def _createTables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                userId          INTEGER PRIMARY KEY,
                username        TEXT,
                firstName       TEXT,
                lastName        TEXT,
                joinedAt        REAL NOT NULL,
                isBanned        INTEGER NOT NULL DEFAULT 0,
                dailyLimit      INTEGER NOT NULL DEFAULT 10,
                testsToday      INTEGER NOT NULL DEFAULT 0,
                testsTotal      INTEGER NOT NULL DEFAULT 0,
                lastResetDate   TEXT NOT NULL DEFAULT '',
                streakDays      INTEGER NOT NULL DEFAULT 0,
                lastStreakDate  TEXT NOT NULL DEFAULT '',
                referredBy      INTEGER,
                bonusTests      INTEGER NOT NULL DEFAULT 0,
                totalOtpHits    INTEGER NOT NULL DEFAULT 0,
                totalReqs       INTEGER NOT NULL DEFAULT 0
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
                apiSnapshot TEXT
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
            CREATE TABLE IF NOT EXISTS botSettings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favoriteNumbers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                userId      INTEGER NOT NULL,
                phone       TEXT NOT NULL,
                label       TEXT NOT NULL DEFAULT '',
                addedAt     REAL NOT NULL,
                UNIQUE(userId, phone)
            );
            CREATE TABLE IF NOT EXISTS testPresets (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                userId      INTEGER NOT NULL,
                name        TEXT NOT NULL,
                phone       TEXT NOT NULL,
                duration    INTEGER NOT NULL,
                workers     INTEGER NOT NULL,
                createdAt   REAL NOT NULL,
                UNIQUE(userId, name)
            );
            CREATE TABLE IF NOT EXISTS referrals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                referrerId  INTEGER NOT NULL,
                referreeId  INTEGER NOT NULL UNIQUE,
                createdAt   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS apiUsageStats (
                name        TEXT PRIMARY KEY,
                totalReqs   INTEGER NOT NULL DEFAULT 0,
                totalOtps   INTEGER NOT NULL DEFAULT 0,
                totalErrors INTEGER NOT NULL DEFAULT 0,
                lastUsedAt  REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scheduledTests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                userId      INTEGER NOT NULL,
                phone       TEXT NOT NULL,
                duration    INTEGER NOT NULL,
                workers     INTEGER NOT NULL,
                runAt       REAL NOT NULL,
                triggered   INTEGER NOT NULL DEFAULT 0,
                createdAt   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS abuseLog (
                userId          INTEGER PRIMARY KEY,
                limitHitStreak  INTEGER NOT NULL DEFAULT 0,
                lastHitDate     TEXT NOT NULL DEFAULT ''
            );
        """)
        migrations = [
            "ALTER TABLE users ADD COLUMN testsTotal INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN streakDays INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN lastStreakDate TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN referredBy INTEGER",
            "ALTER TABLE users ADD COLUMN bonusTests INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN totalOtpHits INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN totalReqs INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE testHistory ADD COLUMN apiSnapshot TEXT",
        ]
        for sql in migrations:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except Exception:
                pass
        self._conn.sync()

    # ------------------------------------------------------------------
    # Internal helpers — batched sync for speed
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> Any:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            self._writeCount += 1
            if self._writeCount >= SYNC_EVERY_N_WRITES:
                try:
                    self._conn.sync()
                except Exception:
                    pass
                self._writeCount = 0
            return cur

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def _fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        with self._lock:
            cur  = self._conn.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]

    def _forceSync(self) -> None:
        """Force immediate sync to Turso — call for critical writes."""
        with self._lock:
            try:
                self._conn.sync()
                self._writeCount = 0
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Bot settings
    # ------------------------------------------------------------------

    def getSetting(self, key: str, default: str = "") -> str:
        row = self._fetchone("SELECT value FROM botSettings WHERE key=?", (key,))
        return row["value"] if row else default

    def setSetting(self, key: str, value: str) -> None:
        self._execute("INSERT OR REPLACE INTO botSettings (key, value) VALUES (?, ?)", (key, value))
        self._forceSync()

    def isMaintenanceMode(self) -> bool:
        return self.getSetting("maintenanceMode", "0") == "1"

    def setMaintenanceMode(self, enabled: bool) -> None:
        self.setSetting("maintenanceMode", "1" if enabled else "0")

    def getMaintenanceMessage(self) -> str:
        return self.getSetting("maintenanceMsg", "Bot is under maintenance. Please try again later.")

    def setMaintenanceMessage(self, msg: str) -> None:
        self.setSetting("maintenanceMsg", msg)

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def registerUser(self, userId: int, username: Optional[str], firstName: str, lastName: Optional[str]) -> bool:
        existing = self._fetchone("SELECT userId FROM users WHERE userId = ?", (userId,))
        if existing:
            self._execute(
                "UPDATE users SET username=?, firstName=?, lastName=? WHERE userId=?",
                (username, firstName, lastName or "", userId)
            )
            return False
        self._execute(
            "INSERT INTO users (userId, username, firstName, lastName, joinedAt, dailyLimit, lastResetDate, testsToday, testsTotal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
            (userId, username, firstName, lastName or "", time.time(), DEFAULT_DAILY_LIMIT, getIstToday())
        )
        self._forceSync()
        return True

    def getUser(self, userId: int) -> Optional[Dict[str, Any]]:
        return self._fetchone("SELECT * FROM users WHERE userId = ?", (userId,))

    def getAllUsers(self, offset: int = 0, limit: int = 10) -> List[Dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM users ORDER BY joinedAt DESC LIMIT ? OFFSET ?", (limit, offset)
        )

    def getUserCount(self) -> int:
        row = self._fetchone("SELECT COUNT(*) as cnt FROM users")
        return row["cnt"] if row else 0

    def searchUsers(self, query: str) -> List[Dict[str, Any]]:
        q = f"%{query}%"
        return self._fetchall(
            "SELECT * FROM users WHERE username LIKE ? OR firstName LIKE ? OR CAST(userId AS TEXT) LIKE ? LIMIT 20",
            (q, q, q)
        )

    def banUser(self, userId: int) -> None:
        self._execute("UPDATE users SET isBanned=1 WHERE userId=?", (userId,))
        self._forceSync()

    def unbanUser(self, userId: int) -> None:
        self._execute("UPDATE users SET isBanned=0 WHERE userId=?", (userId,))
        self._forceSync()

    def setDailyLimit(self, userId: int, limit: int) -> None:
        self._execute("UPDATE users SET dailyLimit=? WHERE userId=?", (limit, userId))
        self._forceSync()

    def setGlobalDailyLimit(self, limit: int) -> None:
        self._execute("UPDATE users SET dailyLimit=?", (limit,))
        self._forceSync()

    def getTopUsers(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM users ORDER BY testsTotal DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------
    # Daily limit + streak
    # ------------------------------------------------------------------

    def _ensureResetForUser(self, userId: int) -> None:
        today = getIstToday()
        row   = self._fetchone("SELECT lastResetDate FROM users WHERE userId=?", (userId,))
        if row and row["lastResetDate"] != today:
            self._execute(
                "UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?",
                (today, userId)
            )

    def _updateStreak(self, userId: int) -> None:
        today     = getIstToday()
        yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
        row = self._fetchone("SELECT streakDays, lastStreakDate FROM users WHERE userId=?", (userId,))
        if not row:
            return
        last = row["lastStreakDate"]
        if last == today:
            return
        if last == yesterday:
            self._execute(
                "UPDATE users SET streakDays=streakDays+1, lastStreakDate=? WHERE userId=?",
                (today, userId)
            )
        else:
            self._execute(
                "UPDATE users SET streakDays=1, lastStreakDate=? WHERE userId=?",
                (today, userId)
            )

    def canRunTest(self, userId: int) -> tuple:
        self._ensureResetForUser(userId)
        row = self._fetchone(
            "SELECT testsToday, dailyLimit, isBanned, bonusTests FROM users WHERE userId=?", (userId,)
        )
        if not row:
            return False, 0, 0
        if row["isBanned"]:
            return False, row["testsToday"], row["dailyLimit"]
        effectiveLimit = row["dailyLimit"] + row["bonusTests"]
        return row["testsToday"] < effectiveLimit, row["testsToday"], effectiveLimit

    def incrementTestCount(self, userId: int) -> None:
        self._ensureResetForUser(userId)
        self._updateStreak(userId)
        self._execute(
            "UPDATE users SET testsToday=testsToday+1, testsTotal=testsTotal+1 WHERE userId=?",
            (userId,)
        )

    def updateUserStats(self, userId: int, reqs: int, otps: int) -> None:
        self._execute(
            "UPDATE users SET totalReqs=totalReqs+?, totalOtpHits=totalOtpHits+? WHERE userId=?",
            (reqs, otps, userId)
        )

    def resetUserTests(self, userId: int) -> None:
        self._execute(
            "UPDATE users SET testsToday=0, lastResetDate=? WHERE userId=?",
            (getIstToday(), userId)
        )
        self._forceSync()

    def resetAllTests(self) -> None:
        self._execute("UPDATE users SET testsToday=0, lastResetDate=?", (getIstToday(),))
        self._forceSync()

    # ------------------------------------------------------------------
    # Test history
    # ------------------------------------------------------------------

    def startTestRecord(self, userId: int, phone: str, duration: int, workers: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO testHistory (userId, phone, duration, workers, startedAt) VALUES (?, ?, ?, ?, ?)",
                (userId, phone, duration, workers, time.time())
            )
            self._conn.commit()
            self._writeCount += 1
            return cur.lastrowid

    def finishTestRecord(self, recordId: int, totalReqs: int, otpHits: int, errors: int, rps: float, apiSnapshot: str = "") -> None:
        self._execute(
            "UPDATE testHistory SET totalReqs=?, otpHits=?, errors=?, rps=?, finishedAt=?, apiSnapshot=? WHERE id=?",
            (totalReqs, otpHits, errors, rps, time.time(), apiSnapshot, recordId)
        )
        self._forceSync()

    def getUserHistory(self, userId: int, limit: int = 10) -> List[Dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM testHistory WHERE userId=? ORDER BY startedAt DESC LIMIT ?",
            (userId, limit)
        )

    def getTestRecord(self, recordId: int) -> Optional[Dict[str, Any]]:
        return self._fetchone("SELECT * FROM testHistory WHERE id=?", (recordId,))

    def getAnalytics(self) -> Dict[str, Any]:
        totalTests = self._fetchone("SELECT COUNT(*) as cnt FROM testHistory WHERE finishedAt IS NOT NULL")
        totalReqs  = self._fetchone("SELECT SUM(totalReqs) as s FROM testHistory")
        totalOtps  = self._fetchone("SELECT SUM(otpHits) as s FROM testHistory")
        todayStart = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        todayTests = self._fetchone("SELECT COUNT(*) as cnt FROM testHistory WHERE startedAt >= ?", (todayStart,))
        todayReqs  = self._fetchone("SELECT SUM(totalReqs) as s FROM testHistory WHERE startedAt >= ?", (todayStart,))
        return {
            "totalTests": totalTests["cnt"] if totalTests else 0,
            "totalReqs":  totalReqs["s"] or 0 if totalReqs else 0,
            "totalOtps":  totalOtps["s"] or 0 if totalOtps else 0,
            "todayTests": todayTests["cnt"] if todayTests else 0,
            "todayReqs":  todayReqs["s"] or 0 if todayReqs else 0,
        }

    # ------------------------------------------------------------------
    # Custom APIs
    # ------------------------------------------------------------------

    def addCustomApi(self, name: str, method: str, url: str, configJson: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO customApis (name, method, url, configJson, addedAt) VALUES (?, ?, ?, ?, ?)",
                (name, method, url, configJson, time.time())
            )
            self._conn.commit()
            self._conn.sync()
            return cur.lastrowid

    def getAllCustomApis(self) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM customApis ORDER BY addedAt DESC")

    def getCustomApi(self, apiId: int) -> Optional[Dict[str, Any]]:
        return self._fetchone("SELECT * FROM customApis WHERE id=?", (apiId,))

    def updateCustomApi(self, apiId: int, name: str, method: str, url: str, configJson: str) -> None:
        self._execute(
            "UPDATE customApis SET name=?, method=?, url=?, configJson=? WHERE id=?",
            (name, method, url, configJson, apiId)
        )
        self._forceSync()

    def deleteCustomApi(self, apiId: int) -> None:
        self._execute("DELETE FROM customApis WHERE id=?", (apiId,))
        self._forceSync()

    # ------------------------------------------------------------------
    # API usage stats
    # ------------------------------------------------------------------

    def recordApiUsage(self, name: str, reqs: int, otps: int, errors: int) -> None:
        self._execute(
            """INSERT INTO apiUsageStats (name, totalReqs, totalOtps, totalErrors, lastUsedAt)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 totalReqs=totalReqs+excluded.totalReqs,
                 totalOtps=totalOtps+excluded.totalOtps,
                 totalErrors=totalErrors+excluded.totalErrors,
                 lastUsedAt=excluded.lastUsedAt""",
            (name, reqs, otps, errors, time.time())
        )

    def getTopApis(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM apiUsageStats ORDER BY totalOtps DESC LIMIT ?", (limit,))

    def getAllApiStats(self) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM apiUsageStats ORDER BY totalReqs DESC")

    # ------------------------------------------------------------------
    # Proxy files
    # ------------------------------------------------------------------

    def addProxyFile(self, label: str, content: str, proxyCount: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO proxyFiles (label, content, proxyCount, uploadedAt) VALUES (?, ?, ?, ?)",
                (label, content, proxyCount, time.time())
            )
            self._conn.commit()
            self._conn.sync()
            return cur.lastrowid

    def getAllProxyFiles(self) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM proxyFiles ORDER BY uploadedAt DESC")

    def getProxyFile(self, fileId: int) -> Optional[Dict[str, Any]]:
        return self._fetchone("SELECT * FROM proxyFiles WHERE id=?", (fileId,))

    def deleteProxyFile(self, fileId: int) -> None:
        self._execute("DELETE FROM proxyFiles WHERE id=?", (fileId,))
        self._forceSync()

    def getAllProxies(self) -> List[str]:
        rows = self._fetchall("SELECT content FROM proxyFiles")
        proxies = []
        for r in rows:
            for line in r["content"].splitlines():
                line = line.strip()
                if line:
                    proxies.append(line)
        return proxies

    # ------------------------------------------------------------------
    # Phone blacklist
    # ------------------------------------------------------------------

    def blacklistPhone(self, phone: str, reason: str = "") -> None:
        self._execute(
            "INSERT OR REPLACE INTO blacklistedPhones (phone, reason, addedAt) VALUES (?,?,?)",
            (phone, reason, time.time())
        )
        self._forceSync()

    def unblacklistPhone(self, phone: str) -> None:
        self._execute("DELETE FROM blacklistedPhones WHERE phone=?", (phone,))
        self._forceSync()

    def isPhoneBlacklisted(self, phone: str) -> bool:
        return self._fetchone("SELECT phone FROM blacklistedPhones WHERE phone=?", (phone,)) is not None

    def getAllBlacklisted(self) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM blacklistedPhones ORDER BY addedAt DESC")

    # ------------------------------------------------------------------
    # Skipped APIs
    # ------------------------------------------------------------------

    def skipApi(self, name: str) -> None:
        self._execute("INSERT OR REPLACE INTO skippedApis (name, addedAt) VALUES (?,?)", (name, time.time()))
        self._forceSync()

    def unskipApi(self, name: str) -> None:
        self._execute("DELETE FROM skippedApis WHERE name=?", (name,))
        self._forceSync()

    def getSkippedApiNames(self) -> set:
        rows = self._fetchall("SELECT name FROM skippedApis")
        return {r["name"] for r in rows}

    def isApiSkipped(self, name: str) -> bool:
        return self._fetchone("SELECT name FROM skippedApis WHERE name=?", (name,)) is not None

    # ------------------------------------------------------------------
    # Favorite numbers
    # ------------------------------------------------------------------

    def getFavorites(self, userId: int) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM favoriteNumbers WHERE userId=? ORDER BY addedAt DESC", (userId,))

    def addFavorite(self, userId: int, phone: str, label: str = "") -> bool:
        if len(self._fetchall("SELECT id FROM favoriteNumbers WHERE userId=?", (userId,))) >= 3:
            return False
        try:
            self._execute(
                "INSERT INTO favoriteNumbers (userId, phone, label, addedAt) VALUES (?,?,?,?)",
                (userId, phone, label, time.time())
            )
            return True
        except Exception:
            return False

    def removeFavorite(self, userId: int, phone: str) -> None:
        self._execute("DELETE FROM favoriteNumbers WHERE userId=? AND phone=?", (userId, phone))

    def isFavorite(self, userId: int, phone: str) -> bool:
        return self._fetchone(
            "SELECT id FROM favoriteNumbers WHERE userId=? AND phone=?", (userId, phone)
        ) is not None

    # ------------------------------------------------------------------
    # Test presets
    # ------------------------------------------------------------------

    def getPresets(self, userId: int) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM testPresets WHERE userId=? ORDER BY createdAt DESC", (userId,))

    def addPreset(self, userId: int, name: str, phone: str, duration: int, workers: int) -> bool:
        if len(self._fetchall("SELECT id FROM testPresets WHERE userId=?", (userId,))) >= 5:
            return False
        try:
            self._execute(
                "INSERT INTO testPresets (userId, name, phone, duration, workers, createdAt) VALUES (?,?,?,?,?,?)",
                (userId, name, phone, duration, workers, time.time())
            )
            return True
        except Exception:
            return False

    def deletePreset(self, userId: int, presetId: int) -> None:
        self._execute("DELETE FROM testPresets WHERE id=? AND userId=?", (presetId, userId))

    def getPreset(self, presetId: int) -> Optional[Dict[str, Any]]:
        return self._fetchone("SELECT * FROM testPresets WHERE id=?", (presetId,))

    # ------------------------------------------------------------------
    # Referrals
    # ------------------------------------------------------------------

    def getReferralCode(self, userId: int) -> str:
        return f"ref_{userId}"

    def applyReferral(self, referrerId: int, referreeId: int) -> bool:
        if referrerId == referreeId:
            return False
        if self._fetchone("SELECT id FROM referrals WHERE referreeId=?", (referreeId,)):
            return False
        self._execute(
            "INSERT INTO referrals (referrerId, referreeId, createdAt) VALUES (?,?,?)",
            (referrerId, referreeId, time.time())
        )
        self._execute("UPDATE users SET bonusTests=bonusTests+3 WHERE userId=?", (referrerId,))
        self._execute("UPDATE users SET bonusTests=bonusTests+1 WHERE userId=?", (referreeId,))
        self._forceSync()
        return True

    def getReferralCount(self, userId: int) -> int:
        row = self._fetchone("SELECT COUNT(*) as cnt FROM referrals WHERE referrerId=?", (userId,))
        return row["cnt"] if row else 0

    def getReferrals(self, userId: int) -> List[Dict[str, Any]]:
        return self._fetchall("SELECT * FROM referrals WHERE referrerId=? ORDER BY createdAt DESC", (userId,))

    # ------------------------------------------------------------------
    # Scheduled tests
    # ------------------------------------------------------------------

    def addScheduledTest(self, userId: int, phone: str, duration: int, workers: int, runAt: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO scheduledTests (userId, phone, duration, workers, runAt, createdAt) VALUES (?,?,?,?,?,?)",
                (userId, phone, duration, workers, runAt, time.time())
            )
            self._conn.commit()
            self._conn.sync()
            return cur.lastrowid

    def getDueScheduledTests(self) -> List[Dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM scheduledTests WHERE triggered=0 AND runAt <= ? ORDER BY runAt ASC",
            (time.time(),)
        )

    def getScheduledTests(self, userId: int) -> List[Dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM scheduledTests WHERE userId=? AND triggered=0 ORDER BY runAt ASC",
            (userId,)
        )

    def markScheduledTestTriggered(self, schedId: int) -> None:
        self._execute("UPDATE scheduledTests SET triggered=1 WHERE id=?", (schedId,))

    def deleteScheduledTest(self, schedId: int, userId: int) -> None:
        self._execute(
            "DELETE FROM scheduledTests WHERE id=? AND userId=? AND triggered=0",
            (schedId, userId)
        )

    def deleteAllScheduledTests(self, userId: int) -> None:
        self._execute("DELETE FROM scheduledTests WHERE userId=? AND triggered=0", (userId,))

    # ------------------------------------------------------------------
    # Auto-ban / abuse tracking
    # ------------------------------------------------------------------

    def recordLimitHit(self, userId: int) -> None:
        today     = getIstToday()
        yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
        row = self._fetchone("SELECT * FROM abuseLog WHERE userId=?", (userId,))
        if not row:
            self._execute(
                "INSERT INTO abuseLog (userId, limitHitStreak, lastHitDate) VALUES (?,1,?)",
                (userId, today)
            )
            return
        if row["lastHitDate"] == today:
            return
        if row["lastHitDate"] == yesterday:
            self._execute(
                "UPDATE abuseLog SET limitHitStreak=limitHitStreak+1, lastHitDate=? WHERE userId=?",
                (today, userId)
            )
        else:
            self._execute(
                "UPDATE abuseLog SET limitHitStreak=1, lastHitDate=? WHERE userId=?",
                (today, userId)
            )

    def getAbuseFlaggedUsers(self, streakThreshold: int = 3) -> List[Dict[str, Any]]:
        return self._fetchall(
            "SELECT a.*, u.firstName, u.username "
            "FROM abuseLog a JOIN users u ON a.userId=u.userId "
            "WHERE a.limitHitStreak >= ?",
            (streakThreshold,)
        )

    def clearAbuseStreak(self, userId: int) -> None:
        self._execute("DELETE FROM abuseLog WHERE userId=?", (userId,))

    def close(self) -> None:
        try:
            self._conn.sync()
            self._conn.close()
        except Exception:
            pass


db = Database()