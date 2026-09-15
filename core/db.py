"""
Himyar Economy — storage layer.

Every row is keyed by guild_id: a member's coins on one server have nothing to do
with their coins on another. Nothing about any single server is hardcoded.

Money movements go through the transfer helpers rather than through raw updates,
so both sides of a payment happen under one lock and coins can't be duplicated by
two commands landing at the same moment.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from typing import Any, Optional

import aiosqlite

DEFAULT_DB_PATH = os.environ.get("HIMYAR_DB_PATH", "data/economy.db")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat()


def parse_ts(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def parse_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def today_key(now: Optional[dt.datetime] = None) -> str:
    return (now or utcnow()).astimezone(dt.timezone.utc).strftime("%Y-%m-%d")


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS guilds (
    guild_id              INTEGER PRIMARY KEY,
    currency_name         TEXT    NOT NULL DEFAULT 'Coins',
    role_multipliers      TEXT    NOT NULL DEFAULT '{}',
    currency_emoji        TEXT    NOT NULL DEFAULT '🪙',
    start_balance         INTEGER NOT NULL DEFAULT 0,

    chat_enabled          INTEGER NOT NULL DEFAULT 1,
    chat_min              INTEGER NOT NULL DEFAULT 1,
    chat_max              INTEGER NOT NULL DEFAULT 3,
    chat_cooldown         INTEGER NOT NULL DEFAULT 60,
    voice_enabled         INTEGER NOT NULL DEFAULT 1,
    voice_per_minute      INTEGER NOT NULL DEFAULT 1,

    daily_amount          INTEGER NOT NULL DEFAULT 100,
    daily_streak_bonus    INTEGER NOT NULL DEFAULT 10,
    daily_streak_cap      INTEGER NOT NULL DEFAULT 500,
    work_enabled          INTEGER NOT NULL DEFAULT 1,
    work_min              INTEGER NOT NULL DEFAULT 50,
    work_max              INTEGER NOT NULL DEFAULT 150,
    work_cooldown         INTEGER NOT NULL DEFAULT 3600,
    daily_earn_cap        INTEGER NOT NULL DEFAULT 2000,

    bank_enabled          INTEGER NOT NULL DEFAULT 1,
    pay_enabled           INTEGER NOT NULL DEFAULT 1,
    pay_min_account_days  INTEGER NOT NULL DEFAULT 7,
    pay_daily_limit       INTEGER NOT NULL DEFAULT 5000,
    pay_tax_percent       REAL    NOT NULL DEFAULT 0,

    rob_enabled           INTEGER NOT NULL DEFAULT 1,
    rob_success_percent   REAL    NOT NULL DEFAULT 35,
    rob_max_percent       REAL    NOT NULL DEFAULT 20,
    rob_fine_percent      REAL    NOT NULL DEFAULT 10,
    rob_cooldown          INTEGER NOT NULL DEFAULT 21600,
    rob_victim_protect    INTEGER NOT NULL DEFAULT 86400,
    rob_min_victim_wallet INTEGER NOT NULL DEFAULT 100,

    games_enabled         INTEGER NOT NULL DEFAULT 1,
    trivia_reward         INTEGER NOT NULL DEFAULT 50,
    trivia_seconds        INTEGER NOT NULL DEFAULT 30,
    guess_cost            INTEGER NOT NULL DEFAULT 20,
    guess_max             INTEGER NOT NULL DEFAULT 100,
    guess_seconds         INTEGER NOT NULL DEFAULT 120,
    duel_enabled          INTEGER NOT NULL DEFAULT 1,
    duel_max_stake        INTEGER NOT NULL DEFAULT 1000,
    heist_base            INTEGER NOT NULL DEFAULT 100,
    heist_min_players     INTEGER NOT NULL DEFAULT 3,
    heist_bonus_percent   REAL    NOT NULL DEFAULT 25,
    heist_seconds         INTEGER NOT NULL DEFAULT 90,
    heist_cooldown        INTEGER NOT NULL DEFAULT 3600,

    log_channel_id        INTEGER,
    staff_role_id         INTEGER,
    embed_color           INTEGER NOT NULL DEFAULT 2003199,
    setup_complete        INTEGER NOT NULL DEFAULT 0,
    last_heist            TEXT,
    created_at            TEXT
);

CREATE TABLE IF NOT EXISTS members (
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    wallet        INTEGER NOT NULL DEFAULT 0,
    bank          INTEGER NOT NULL DEFAULT 0,
    total_earned  INTEGER NOT NULL DEFAULT 0,
    earned_today  INTEGER NOT NULL DEFAULT 0,
    earn_day      TEXT,
    paid_today    INTEGER NOT NULL DEFAULT 0,
    pay_day       TEXT,
    last_daily    TEXT,
    streak        INTEGER NOT NULL DEFAULT 0,
    best_streak   INTEGER NOT NULL DEFAULT 0,
    last_work     TEXT,
    last_rob      TEXT,
    last_robbed   TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_members_rich ON members(guild_id, wallet DESC, bank DESC);

CREATE TABLE IF NOT EXISTS shop_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT,
    price       INTEGER NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'role',
    role_id     INTEGER,
    duration    INTEGER NOT NULL DEFAULT 0,
    stock       INTEGER NOT NULL DEFAULT -1,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_shop_guild ON shop_items(guild_id, enabled);

CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    item_id    INTEGER,
    item_name  TEXT,
    kind       TEXT,
    price      INTEGER NOT NULL DEFAULT 0,
    fulfilled  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_purchases_guild ON purchases(guild_id, fulfilled);

CREATE TABLE IF NOT EXISTS temp_roles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    role_id    INTEGER NOT NULL,
    expires_at TEXT    NOT NULL,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_temp_expiry ON temp_roles(expires_at);

CREATE TABLE IF NOT EXISTS transactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    target_id  INTEGER,
    kind       TEXT    NOT NULL,
    amount     INTEGER NOT NULL,
    note       TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tx_guild ON transactions(guild_id, id DESC);

CREATE TABLE IF NOT EXISTS trivia_questions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL DEFAULT 0,
    question   TEXT    NOT NULL,
    answers    TEXT    NOT NULL,
    correct    INTEGER NOT NULL DEFAULT 0,
    category   TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_trivia_guild ON trivia_questions(guild_id);

CREATE TABLE IF NOT EXISTS games (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    kind       TEXT    NOT NULL,
    state      TEXT    NOT NULL DEFAULT '{}',
    pot        INTEGER NOT NULL DEFAULT 0,
    status     TEXT    NOT NULL DEFAULT 'running',
    ends_at    TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(status, ends_at);

CREATE TABLE IF NOT EXISTS game_players (
    game_id   INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    stake     INTEGER NOT NULL DEFAULT 0,
    data      TEXT,
    joined_at TEXT,
    PRIMARY KEY (game_id, user_id)
);

CREATE TABLE IF NOT EXISTS strings (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);
"""

GUILD_DEFAULTS = {
    "currency_name": "Coins", "currency_emoji": "🪙", "start_balance": 0,
    "role_multipliers": "{}",
    "chat_enabled": 1, "chat_min": 1, "chat_max": 3, "chat_cooldown": 60,
    "voice_enabled": 1, "voice_per_minute": 1,
    "daily_amount": 100, "daily_streak_bonus": 10, "daily_streak_cap": 500,
    "work_enabled": 1, "work_min": 50, "work_max": 150, "work_cooldown": 3600,
    "daily_earn_cap": 2000,
    "bank_enabled": 1, "pay_enabled": 1, "pay_min_account_days": 7,
    "pay_daily_limit": 5000, "pay_tax_percent": 0.0,
    "rob_enabled": 1, "rob_success_percent": 35.0, "rob_max_percent": 20.0,
    "rob_fine_percent": 10.0, "rob_cooldown": 21600, "rob_victim_protect": 86400,
    "rob_min_victim_wallet": 100,
    "games_enabled": 1, "trivia_reward": 50, "trivia_seconds": 30,
    "guess_cost": 20, "guess_max": 100, "guess_seconds": 120,
    "duel_enabled": 1, "duel_max_stake": 1000,
    "heist_base": 100, "heist_min_players": 3, "heist_bonus_percent": 25.0,
    "heist_seconds": 90, "heist_cooldown": 3600,
    "log_channel_id": None, "staff_role_id": None,
    "embed_color": 0x1E90FF, "setup_complete": 0, "last_heist": None,
}
GUILD_COLUMNS = set(GUILD_DEFAULTS)

# Columns added after the first release. CREATE TABLE IF NOT EXISTS never touches
# a table that already exists, so a live database needs them added explicitly.
GUILD_MIGRATIONS = {
    "role_multipliers": "TEXT NOT NULL DEFAULT '{}'",
}
MEMBER_COLUMNS = {
    "wallet", "bank", "total_earned", "earned_today", "earn_day", "paid_today",
    "pay_day", "last_daily", "streak", "best_streak", "last_work", "last_rob",
    "last_robbed",
}
ITEM_COLUMNS = {"name", "description", "price", "kind", "role_id", "duration",
                "stock", "enabled"}


class InsufficientFunds(Exception):
    """Raised when a debit would take a balance below zero."""


class Database:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    # ─── lifecycle ────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Add any columns introduced after this database was first created."""
        async with self._conn.execute("PRAGMA table_info(guilds)") as cur:
            existing = {row[1] for row in await cur.fetchall()}
        for column, ddl in GUILD_MIGRATIONS.items():
            if column not in existing:
                await self._conn.execute(
                    f"ALTER TABLE guilds ADD COLUMN {column} {ddl}"
                )

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() was never awaited")
        return self._conn

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        async with self.conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        async with self.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _execute(self, sql: str, params: tuple = ()) -> int:
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            await self.conn.commit()
            return cur.lastrowid

    # ─── guild settings ───────────────────────────────────────────────────────
    async def get_guild(self, guild_id: int) -> dict:
        row = await self._fetchone("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        if row is None:
            await self._execute(
                "INSERT OR IGNORE INTO guilds (guild_id, created_at) VALUES (?, ?)",
                (guild_id, iso(utcnow())),
            )
            row = await self._fetchone("SELECT * FROM guilds WHERE guild_id = ?", (guild_id,))
        return row or {"guild_id": guild_id, **GUILD_DEFAULTS}

    async def update_guild(self, guild_id: int, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in GUILD_COLUMNS}
        if not fields:
            return
        await self.get_guild(guild_id)
        if isinstance(fields.get("last_heist"), dt.datetime):
            fields["last_heist"] = iso(fields["last_heist"])
        if isinstance(fields.get("role_multipliers"), dict):
            fields["role_multipliers"] = json.dumps(
                {str(k): float(v) for k, v in fields["role_multipliers"].items()}
            )
        assignments = ", ".join(f"{k} = ?" for k in fields)
        await self._execute(
            f"UPDATE guilds SET {assignments} WHERE guild_id = ?",
            (*fields.values(), guild_id),
        )

    async def get_role_multipliers(self, guild_id: int) -> dict[int, float]:
        settings = await self.get_guild(guild_id)
        raw = settings.get("role_multipliers")
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            data = {}
        return {int(k): float(v) for k, v in data.items()}

    async def set_role_multipliers(self, guild_id: int,
                                   mults: dict[int, float]) -> None:
        await self.update_guild(guild_id, role_multipliers=mults)

    async def wipe_guild(self, guild_id: int) -> None:
        await self._execute(
            "DELETE FROM game_players WHERE game_id IN "
            "(SELECT id FROM games WHERE guild_id = ?)", (guild_id,)
        )
        for table in ("members", "shop_items", "purchases", "temp_roles",
                      "transactions", "trivia_questions", "games", "strings", "guilds"):
            await self._execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))

    # ─── members ──────────────────────────────────────────────────────────────
    async def get_member(self, guild_id: int, user_id: int) -> dict:
        row = await self._fetchone(
            "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        if row is None:
            settings = await self.get_guild(guild_id)
            now = iso(utcnow())
            await self._execute(
                "INSERT OR IGNORE INTO members (guild_id, user_id, wallet, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (guild_id, user_id, int(settings.get("start_balance") or 0), now, now),
            )
            row = await self._fetchone(
                "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
            )
        return row or {"guild_id": guild_id, "user_id": user_id, "wallet": 0, "bank": 0}

    async def peek_member(self, guild_id: int, user_id: int) -> Optional[dict]:
        return await self._fetchone(
            "SELECT * FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    async def update_member(self, guild_id: int, user_id: int, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in MEMBER_COLUMNS}
        if not fields:
            return
        await self.get_member(guild_id, user_id)
        for key, value in list(fields.items()):
            if isinstance(value, dt.datetime):
                fields[key] = iso(value)
            elif isinstance(value, dt.date):
                fields[key] = value.isoformat()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        await self._execute(
            f"UPDATE members SET {assignments}, updated_at = ? WHERE guild_id = ? AND user_id = ?",
            (*fields.values(), iso(utcnow()), guild_id, user_id),
        )

    # ─── money movements ──────────────────────────────────────────────────────
    async def credit(self, guild_id: int, user_id: int, amount: int, *,
                     kind: str = "credit", note: str = "",
                     counts_as_earned: bool = False, day: Optional[str] = None,
                     target_id: Optional[int] = None) -> dict:
        """Add coins to a wallet, optionally counting toward the daily earn cap."""
        amount = int(amount)
        if amount <= 0:
            return await self.get_member(guild_id, user_id)
        await self.get_member(guild_id, user_id)
        day = day or today_key()
        async with self._lock:
            if counts_as_earned:
                await self.conn.execute(
                    "UPDATE members SET earned_today = CASE WHEN earn_day = ? "
                    "THEN earned_today + ? ELSE ? END, earn_day = ?, "
                    "total_earned = total_earned + ? "
                    "WHERE guild_id = ? AND user_id = ?",
                    (day, amount, amount, day, amount, guild_id, user_id),
                )
            await self.conn.execute(
                "UPDATE members SET wallet = wallet + ?, updated_at = ? "
                "WHERE guild_id = ? AND user_id = ?",
                (amount, iso(utcnow()), guild_id, user_id),
            )
            await self.conn.execute(
                "INSERT INTO transactions (guild_id, user_id, target_id, kind, amount, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, target_id, kind, amount, note, iso(utcnow())),
            )
            await self.conn.commit()
        return await self.get_member(guild_id, user_id)

    async def debit(self, guild_id: int, user_id: int, amount: int, *,
                    kind: str = "debit", note: str = "",
                    target_id: Optional[int] = None, allow_partial: bool = False) -> int:
        """Take coins from a wallet. Raises InsufficientFunds unless allow_partial."""
        amount = int(amount)
        if amount <= 0:
            return 0
        member = await self.get_member(guild_id, user_id)
        wallet = int(member.get("wallet") or 0)
        if amount > wallet:
            if not allow_partial:
                raise InsufficientFunds(f"needs {amount}, has {wallet}")
            amount = wallet
        if amount <= 0:
            return 0
        async with self._lock:
            # The wallet >= ? guard makes the debit safe even if another command
            # spent the same coins a moment ago.
            cur = await self.conn.execute(
                "UPDATE members SET wallet = wallet - ?, updated_at = ? "
                "WHERE guild_id = ? AND user_id = ? AND wallet >= ?",
                (amount, iso(utcnow()), guild_id, user_id, amount),
            )
            if cur.rowcount == 0:
                await self.conn.commit()
                raise InsufficientFunds("balance changed during the transaction")
            await self.conn.execute(
                "INSERT INTO transactions (guild_id, user_id, target_id, kind, amount, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, target_id, kind, -amount, note, iso(utcnow())),
            )
            await self.conn.commit()
        return amount

    async def transfer(self, guild_id: int, sender_id: int, recipient_id: int,
                       debit_amount: int, credit_amount: int, *, kind: str = "pay",
                       note: str = "", day: Optional[str] = None) -> bool:
        """Move coins between two members under a single lock.

        Both sides happen together or neither does, so a crash or a race can't
        leave coins duplicated or vanished.
        """
        debit_amount = int(debit_amount)
        credit_amount = int(credit_amount)
        if debit_amount <= 0 or credit_amount < 0 or credit_amount > debit_amount:
            return False
        await self.get_member(guild_id, sender_id)
        await self.get_member(guild_id, recipient_id)
        day = day or today_key()
        stamp = iso(utcnow())
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE members SET wallet = wallet - ?, updated_at = ? "
                "WHERE guild_id = ? AND user_id = ? AND wallet >= ?",
                (debit_amount, stamp, guild_id, sender_id, debit_amount),
            )
            if cur.rowcount == 0:
                await self.conn.commit()
                return False
            await self.conn.execute(
                "UPDATE members SET wallet = wallet + ?, updated_at = ? "
                "WHERE guild_id = ? AND user_id = ?",
                (credit_amount, stamp, guild_id, recipient_id),
            )
            if kind == "pay":
                await self.conn.execute(
                    "UPDATE members SET paid_today = CASE WHEN pay_day = ? "
                    "THEN paid_today + ? ELSE ? END, pay_day = ? "
                    "WHERE guild_id = ? AND user_id = ?",
                    (day, debit_amount, debit_amount, day, guild_id, sender_id),
                )
            await self.conn.execute(
                "INSERT INTO transactions (guild_id, user_id, target_id, kind, amount, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, sender_id, recipient_id, kind, -debit_amount, note, stamp),
            )
            await self.conn.execute(
                "INSERT INTO transactions (guild_id, user_id, target_id, kind, amount, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, recipient_id, sender_id, kind, credit_amount, note, stamp),
            )
            await self.conn.commit()
        return True

    async def move_bank(self, guild_id: int, user_id: int, amount: int,
                        to_bank: bool) -> bool:
        """Deposit or withdraw. Total holdings are unchanged either way."""
        amount = int(amount)
        if amount <= 0:
            return False
        await self.get_member(guild_id, user_id)
        stamp = iso(utcnow())
        async with self._lock:
            if to_bank:
                cur = await self.conn.execute(
                    "UPDATE members SET wallet = wallet - ?, bank = bank + ?, updated_at = ? "
                    "WHERE guild_id = ? AND user_id = ? AND wallet >= ?",
                    (amount, amount, stamp, guild_id, user_id, amount),
                )
            else:
                cur = await self.conn.execute(
                    "UPDATE members SET bank = bank - ?, wallet = wallet + ?, updated_at = ? "
                    "WHERE guild_id = ? AND user_id = ? AND bank >= ?",
                    (amount, amount, stamp, guild_id, user_id, amount),
                )
            moved = cur.rowcount > 0
            if moved:
                await self.conn.execute(
                    "INSERT INTO transactions (guild_id, user_id, kind, amount, note, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (guild_id, user_id, "deposit" if to_bank else "withdraw",
                     amount if to_bank else -amount, "", stamp),
                )
            await self.conn.commit()
        return moved

    async def earned_today(self, guild_id: int, user_id: int,
                           day: Optional[str] = None) -> int:
        member = await self.peek_member(guild_id, user_id)
        if not member:
            return 0
        if member.get("earn_day") != (day or today_key()):
            return 0
        return int(member.get("earned_today") or 0)

    async def paid_today(self, guild_id: int, user_id: int,
                         day: Optional[str] = None) -> int:
        member = await self.peek_member(guild_id, user_id)
        if not member:
            return 0
        if member.get("pay_day") != (day or today_key()):
            return 0
        return int(member.get("paid_today") or 0)

    async def rich_list(self, guild_id: int, limit: int = 10, offset: int = 0) -> list[dict]:
        return await self._fetchall(
            "SELECT *, (wallet + bank) AS total FROM members WHERE guild_id = ? "
            "AND (wallet + bank) > 0 ORDER BY total DESC, user_id ASC LIMIT ? OFFSET ?",
            (guild_id, int(limit), int(offset)),
        )

    async def rich_count(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM members WHERE guild_id = ? AND (wallet + bank) > 0",
            (guild_id,),
        )
        return int(row["n"]) if row else 0

    async def rank_of(self, guild_id: int, user_id: int) -> Optional[int]:
        member = await self.peek_member(guild_id, user_id)
        if not member:
            return None
        total = int(member.get("wallet") or 0) + int(member.get("bank") or 0)
        if total <= 0:
            return None
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM members WHERE guild_id = ? AND (wallet + bank) > ?",
            (guild_id, total),
        )
        return (int(row["n"]) if row else 0) + 1

    async def total_in_circulation(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT COALESCE(SUM(wallet + bank), 0) AS n FROM members WHERE guild_id = ?",
            (guild_id,),
        )
        return int(row["n"]) if row else 0

    async def reset_member(self, guild_id: int, user_id: int) -> None:
        await self._execute(
            "DELETE FROM members WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    async def reset_all_members(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM members WHERE guild_id = ?", (guild_id,)
        )
        count = int(row["n"]) if row else 0
        await self._execute("DELETE FROM members WHERE guild_id = ?", (guild_id,))
        return count

    # ─── shop ─────────────────────────────────────────────────────────────────
    async def add_item(self, guild_id: int, name: str, price: int, **fields: Any) -> int:
        fields = {k: v for k, v in fields.items() if k in ITEM_COLUMNS}
        cols = ["guild_id", "name", "price", "created_at", *fields.keys()]
        placeholders = ", ".join("?" for _ in cols)
        return await self._execute(
            f"INSERT INTO shop_items ({', '.join(cols)}) VALUES ({placeholders})",
            (guild_id, name, int(price), iso(utcnow()), *fields.values()),
        )

    async def get_item(self, item_id: int) -> Optional[dict]:
        return await self._fetchone("SELECT * FROM shop_items WHERE id = ?", (item_id,))

    async def get_items(self, guild_id: int, enabled_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM shop_items WHERE guild_id = ?"
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY price ASC, id ASC"
        return await self._fetchall(sql, (guild_id,))

    async def update_item(self, item_id: int, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in ITEM_COLUMNS}
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        await self._execute(
            f"UPDATE shop_items SET {assignments} WHERE id = ?", (*fields.values(), item_id)
        )

    async def delete_item(self, item_id: int) -> None:
        await self._execute("DELETE FROM shop_items WHERE id = ?", (item_id,))

    async def take_stock(self, item_id: int) -> bool:
        """Decrement stock atomically. True if a unit was available."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT stock FROM shop_items WHERE id = ?", (item_id,)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return False
            if int(row[0]) < 0:
                return True                      # unlimited
            cur = await self.conn.execute(
                "UPDATE shop_items SET stock = stock - 1 WHERE id = ? AND stock > 0",
                (item_id,),
            )
            await self.conn.commit()
            return cur.rowcount > 0

    async def record_purchase(self, guild_id: int, user_id: int, item: dict) -> int:
        return await self._execute(
            "INSERT INTO purchases (guild_id, user_id, item_id, item_name, kind, price, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, item.get("id"), item.get("name"), item.get("kind"),
             int(item.get("price") or 0), iso(utcnow())),
        )

    async def get_purchases(self, guild_id: int, user_id: Optional[int] = None,
                            pending_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM purchases WHERE guild_id = ?"
        params: list[Any] = [guild_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if pending_only:
            sql += " AND fulfilled = 0 AND kind = 'custom'"
        sql += " ORDER BY id DESC"
        return await self._fetchall(sql, tuple(params))

    async def fulfil_purchase(self, purchase_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE purchases SET fulfilled = 1 WHERE id = ? AND fulfilled = 0",
                (purchase_id,),
            )
            await self.conn.commit()
            return cur.rowcount > 0

    # ─── temporary roles ──────────────────────────────────────────────────────
    async def add_temp_role(self, guild_id: int, user_id: int, role_id: int,
                            expires_at: dt.datetime) -> int:
        return await self._execute(
            "INSERT INTO temp_roles (guild_id, user_id, role_id, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, role_id, iso(expires_at), iso(utcnow())),
        )

    async def expired_temp_roles(self, now: Optional[dt.datetime] = None) -> list[dict]:
        return await self._fetchall(
            "SELECT * FROM temp_roles WHERE expires_at <= ?", (iso(now or utcnow()),)
        )

    async def member_temp_roles(self, guild_id: int, user_id: int) -> list[dict]:
        return await self._fetchall(
            "SELECT * FROM temp_roles WHERE guild_id = ? AND user_id = ? ORDER BY expires_at",
            (guild_id, user_id),
        )

    async def delete_temp_role(self, row_id: int) -> None:
        await self._execute("DELETE FROM temp_roles WHERE id = ?", (row_id,))

    # ─── audit log ────────────────────────────────────────────────────────────
    async def recent_transactions(self, guild_id: int, limit: int = 15,
                                  user_id: Optional[int] = None) -> list[dict]:
        sql = "SELECT * FROM transactions WHERE guild_id = ?"
        params: list[Any] = [guild_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return await self._fetchall(sql, tuple(params))

    # ─── trivia questions ─────────────────────────────────────────────────────
    async def add_question(self, guild_id: int, question: str, answers: list[str],
                           correct: int, category: str = "custom") -> int:
        return await self._execute(
            "INSERT INTO trivia_questions (guild_id, question, answers, correct, category, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, question, json.dumps(answers), int(correct), category, iso(utcnow())),
        )

    async def get_questions(self, guild_id: int, include_builtin: bool = True) -> list[dict]:
        sql = "SELECT * FROM trivia_questions WHERE guild_id = ?"
        params: list[Any] = [guild_id]
        if include_builtin:
            sql += " OR guild_id = 0"
        rows = await self._fetchall(sql, tuple(params))
        for row in rows:
            try:
                row["answers"] = json.loads(row["answers"])
            except (TypeError, ValueError):
                row["answers"] = []
        return [r for r in rows if r["answers"]]

    async def delete_question(self, question_id: int, guild_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM trivia_questions WHERE id = ? AND guild_id = ?",
                (question_id, guild_id),
            )
            await self.conn.commit()
            return cur.rowcount > 0

    async def count_custom_questions(self, guild_id: int) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM trivia_questions WHERE guild_id = ?", (guild_id,)
        )
        return int(row["n"]) if row else 0

    async def seed_builtin_questions(self, questions: list[dict]) -> int:
        """Load the built-in bank once. Safe to call on every start."""
        existing = await self._fetchone(
            "SELECT COUNT(*) AS n FROM trivia_questions WHERE guild_id = 0", ()
        )
        if existing and int(existing["n"]) > 0:
            return 0
        for item in questions:
            await self.add_question(
                0, item["question"], item["answers"], item["correct"],
                item.get("category", "general"),
            )
        return len(questions)

    # ─── games ────────────────────────────────────────────────────────────────
    async def create_game(self, guild_id: int, channel_id: int, kind: str,
                          state: dict, ends_at: Optional[dt.datetime] = None,
                          pot: int = 0) -> int:
        return await self._execute(
            "INSERT INTO games (guild_id, channel_id, kind, state, pot, status, ends_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'running', ?, ?)",
            (guild_id, channel_id, kind, json.dumps(state), int(pot),
             iso(ends_at) if ends_at else None, iso(utcnow())),
        )

    async def get_game(self, game_id: int) -> Optional[dict]:
        row = await self._fetchone("SELECT * FROM games WHERE id = ?", (game_id,))
        if row:
            try:
                row["state"] = json.loads(row["state"])
            except (TypeError, ValueError):
                row["state"] = {}
        return row

    async def active_game(self, guild_id: int, kind: str,
                          channel_id: Optional[int] = None) -> Optional[dict]:
        sql = "SELECT * FROM games WHERE guild_id = ? AND kind = ? AND status = 'running'"
        params: list[Any] = [guild_id, kind]
        if channel_id is not None:
            sql += " AND channel_id = ?"
            params.append(channel_id)
        row = await self._fetchone(sql + " ORDER BY id DESC LIMIT 1", tuple(params))
        if row:
            try:
                row["state"] = json.loads(row["state"])
            except (TypeError, ValueError):
                row["state"] = {}
        return row

    async def update_game(self, game_id: int, **fields: Any) -> None:
        allowed = {"message_id", "state", "pot", "status", "ends_at"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        if isinstance(fields.get("state"), (dict, list)):
            fields["state"] = json.dumps(fields["state"])
        if isinstance(fields.get("ends_at"), dt.datetime):
            fields["ends_at"] = iso(fields["ends_at"])
        assignments = ", ".join(f"{k} = ?" for k in fields)
        await self._execute(
            f"UPDATE games SET {assignments} WHERE id = ?", (*fields.values(), game_id)
        )

    async def finish_game(self, game_id: int, status: str = "finished") -> bool:
        """Close a game exactly once. Returns False if it was already closed,
        which is what stops a game paying out twice."""
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE games SET status = ? WHERE id = ? AND status = 'running'",
                (status, game_id),
            )
            await self.conn.commit()
            return cur.rowcount > 0

    async def join_game(self, game_id: int, user_id: int, stake: int = 0,
                        data: Optional[dict] = None) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "INSERT OR IGNORE INTO game_players (game_id, user_id, stake, data, joined_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (game_id, user_id, int(stake), json.dumps(data or {}), iso(utcnow())),
            )
            if cur.rowcount > 0:
                await self.conn.execute(
                    "UPDATE games SET pot = pot + ? WHERE id = ?", (int(stake), game_id)
                )
            await self.conn.commit()
            return cur.rowcount > 0

    async def game_players(self, game_id: int) -> list[dict]:
        rows = await self._fetchall(
            "SELECT * FROM game_players WHERE game_id = ? ORDER BY joined_at ASC", (game_id,)
        )
        for row in rows:
            try:
                row["data"] = json.loads(row["data"]) if row.get("data") else {}
            except (TypeError, ValueError):
                row["data"] = {}
        return rows

    async def set_player_data(self, game_id: int, user_id: int, data: dict) -> None:
        await self._execute(
            "UPDATE game_players SET data = ? WHERE game_id = ? AND user_id = ?",
            (json.dumps(data), game_id, user_id),
        )

    async def unfinished_games(self) -> list[dict]:
        """Games left running when the bot stopped — their stakes need refunding."""
        rows = await self._fetchall("SELECT * FROM games WHERE status = 'running'", ())
        for row in rows:
            try:
                row["state"] = json.loads(row["state"])
            except (TypeError, ValueError):
                row["state"] = {}
        return rows

    # ─── editable strings ─────────────────────────────────────────────────────
    async def get_strings(self, guild_id: int) -> dict[str, str]:
        rows = await self._fetchall(
            "SELECT key, value FROM strings WHERE guild_id = ?", (guild_id,)
        )
        return {r["key"]: r["value"] for r in rows}

    async def set_string(self, guild_id: int, key: str, value: str) -> None:
        await self._execute(
            "INSERT INTO strings (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, value),
        )

    async def reset_string(self, guild_id: int, key: str) -> None:
        await self._execute(
            "DELETE FROM strings WHERE guild_id = ? AND key = ?", (guild_id, key)
        )
