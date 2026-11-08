"""Общая игровая логика для бота и мини-аппа (без привязки к aiohttp)."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

import db
import game

BONUS_COOLDOWN = 86400


class GameError(Exception):
    """Ошибка, текст которой можно показать игроку."""


def money(units: int) -> str:
    return f"{units / 100:,.2f}".replace(",", " ")


# ---------- проверка данных Telegram Mini App ----------

def verify_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict | None:
    """Возвращает данные пользователя из initData, если подпись Telegram верна."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", None)
        if not received:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs["user"])
        return user if isinstance(user.get("id"), int) else None
    except (ValueError, KeyError, TypeError):
        return None


# ---------- игра ----------

def _to_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise GameError(f"Некорректное значение: {field}")
    return int(value)


def play(uid: int, bet, chance, direction) -> dict:
    bet = _to_int(bet, "ставка")
    chance = _to_int(chance, "шанс")
    if direction not in ("under", "over"):
        raise GameError("Некорректное направление")
    if not game.MIN_CHANCE <= chance <= game.MAX_CHANCE:
        raise GameError(f"Шанс должен быть от {game.MIN_CHANCE}% до {game.MAX_CHANCE}%")
    min_bet = int(db.get_setting("min_bet"))
    if bet < min_bet:
        raise GameError(f"Минимальная ставка: {money(min_bet)}")
    u = db.get_user(uid)
    if bet > u["balance"]:
        raise GameError("Недостаточно монет")

    rtp = float(db.get_setting("rtp"))
    number = game.roll(u["server_seed"], u["client_seed"], u["nonce"])
    win = game.is_win(number, chance, direction)
    pay = game.payout(bet, chance, rtp) if win else 0
    db.add_balance(uid, pay - bet)
    db.add_game(uid, bet, chance, direction, number, win, pay, u["nonce"])
    db.update(uid, nonce=u["nonce"] + 1)
    return {
        "number": number,
        "win": win,
        "payout": pay,
        "bet": bet,
        "chance": chance,
        "direction": direction,
        "balance": db.get_user(uid)["balance"],
        "nonce": u["nonce"],
    }


def claim_bonus(uid: int) -> dict:
    u = db.get_user(uid)
    left = u["last_bonus"] + BONUS_COOLDOWN - int(time.time())
    if left > 0:
        raise GameError(f"Бонус будет доступен через {left // 3600} ч {left % 3600 // 60} мин")
    bonus = int(db.get_setting("daily_bonus"))
    db.add_balance(uid, bonus)
    db.update(uid, last_bonus=int(time.time()))
    return {"bonus": bonus, "balance": db.get_user(uid)["balance"]}


def rotate_seeds(uid: int, client_seed: str | None = None) -> dict:
    u = db.get_user(uid)
    client_seed = (client_seed or "").strip()[:32] or game.new_client_seed()
    db.update(
        uid,
        server_seed=u["next_seed"],
        next_seed=game.new_server_seed(),
        client_seed=client_seed,
        nonce=0,
    )
    return {
        "revealed": {
            "server_seed": u["server_seed"],
            "client_seed": u["client_seed"],
            "bets": u["nonce"],
        }
    }


# ---------- данные для экранов ----------

def me_payload(uid: int) -> dict:
    u = db.get_user(uid)
    s = db.user_stats(uid)
    left = max(0, u["last_bonus"] + BONUS_COOLDOWN - int(time.time()))
    return {
        "id": uid,
        "name": u["name"] or u["username"] or "Игрок",
        "username": u["username"],
        "balance": u["balance"],
        "rtp": float(db.get_setting("rtp")),
        "min_bet": int(db.get_setting("min_bet")),
        "min_chance": game.MIN_CHANCE,
        "max_chance": game.MAX_CHANCE,
        "bonus_amount": int(db.get_setting("daily_bonus")),
        "bonus_left": left,
        "joined": u["joined"],
        "stats": {
            "bets": s["n"],
            "turnover": s["turnover"],
            "paid": s["paid"],
            "best": s["best"],
        },
    }


def fair_payload(uid: int) -> dict:
    u = db.get_user(uid)
    return {
        "server_hash": game.seed_hash(u["server_seed"]),
        "client_seed": u["client_seed"],
        "nonce": u["nonce"],
        "next_hash": game.seed_hash(u["next_seed"]),
    }


def history_payload(uid: int, scope: str = "all", limit: int = 20) -> list[dict]:
    rows = db.last_games(uid if scope == "mine" else None, max(1, min(limit, 50)))
    return [
        {
            "name": r["name"] or r["username"] or "Игрок",
            "direction": r["direction"],
            "chance": r["chance"],
            "number": r["number"],
            "win": bool(r["win"]),
            "bet": r["bet"],
            "payout": r["payout"],
            "ts": r["ts"],
        }
        for r in rows
    ]
