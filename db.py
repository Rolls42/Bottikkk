import os
import sqlite3
import time

import game

# На хостинге укажи DB_PATH=/data/bot.db и подключи Volume в /data, чтобы баланс не пропадал
DB_PATH = os.getenv("DB_PATH", "bot.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

DEFAULTS = {
    "rtp": "97",
    "start_balance": "100000",  # 1000.00
    "daily_bonus": "1000",  # 10.00
    "min_bet": "10",  # 0.10
}


def init() -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT,
            balance INTEGER NOT NULL,
            bet INTEGER NOT NULL DEFAULT 100,
            chance INTEGER NOT NULL DEFAULT 50,
            client_seed TEXT NOT NULL,
            server_seed TEXT NOT NULL,
            next_seed TEXT NOT NULL,
            nonce INTEGER NOT NULL DEFAULT 0,
            last_bonus INTEGER NOT NULL DEFAULT 0,
            joined INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bet INTEGER NOT NULL,
            chance INTEGER NOT NULL,
            direction TEXT NOT NULL,
            number INTEGER NOT NULL,
            win INTEGER NOT NULL,
            payout INTEGER NOT NULL,
            nonce INTEGER NOT NULL,
            ts INTEGER NOT NULL
        );
        """
    )
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)")]
    if "name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN name TEXT")
    for k, v in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, v))
    conn.commit()


def get_setting(key: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS[key]


def set_setting(key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
    conn.commit()


def get_user(uid: int, username: str | None = None, name: str | None = None) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO users (id, username, name, balance, client_seed, server_seed, next_seed, joined)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                uid,
                username,
                name,
                int(get_setting("start_balance")),
                game.new_client_seed(),
                game.new_server_seed(),
                game.new_server_seed(),
                int(time.time()),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    elif (username and row["username"] != username) or (name and row["name"] != name):
        conn.execute(
            "UPDATE users SET username=COALESCE(?, username), name=COALESCE(?, name) WHERE id=?",
            (username, name, uid),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return row


def find_user(ref: str) -> sqlite3.Row | None:
    ref = ref.lstrip("@")
    if ref.isdigit():
        return conn.execute("SELECT * FROM users WHERE id=?", (int(ref),)).fetchone()
    return conn.execute(
        "SELECT * FROM users WHERE lower(username)=lower(?)", (ref,)
    ).fetchone()


def update(uid: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE users SET {cols} WHERE id=?", (*fields.values(), uid))
    conn.commit()


def add_balance(uid: int, delta: int) -> None:
    conn.execute("UPDATE users SET balance = balance + ? WHERE id=?", (delta, uid))
    conn.commit()


def add_game(uid, bet, chance, direction, number, win, pay, nonce) -> None:
    conn.execute(
        "INSERT INTO games (user_id, bet, chance, direction, number, win, payout, nonce, ts)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (uid, bet, chance, direction, number, int(win), pay, nonce, int(time.time())),
    )
    conn.commit()


def last_games(uid: int | None = None, limit: int = 10) -> list[sqlite3.Row]:
    if uid is None:
        return conn.execute(
            "SELECT g.*, u.username, u.name FROM games g JOIN users u ON u.id=g.user_id"
            " ORDER BY g.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return conn.execute(
        "SELECT g.*, u.username, u.name FROM games g JOIN users u ON u.id=g.user_id"
        " WHERE g.user_id=? ORDER BY g.id DESC LIMIT ?",
        (uid, limit),
    ).fetchall()


def user_stats(uid: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bet),0) AS turnover,"
        " COALESCE(SUM(payout),0) AS paid,"
        " COALESCE(MAX(CASE WHEN win=1 THEN payout-bet END),0) AS best"
        " FROM games WHERE user_id=?",
        (uid,),
    ).fetchone()


def global_stats() -> sqlite3.Row:
    return conn.execute(
        "SELECT (SELECT COUNT(*) FROM users) AS users, COUNT(*) AS n,"
        " COALESCE(SUM(bet),0) AS turnover, COALESCE(SUM(payout),0) AS paid FROM games"
    ).fetchone()
