"""Логика игры и честность (provably fair). Баланс хранится в сотых долях (1.00 = 100)."""
import hashlib
import hmac
import secrets

MAX_NUMBER = 1_000_000
MIN_CHANCE = 1
MAX_CHANCE = 95


def new_server_seed() -> str:
    return secrets.token_hex(32)


def new_client_seed() -> str:
    return secrets.token_hex(8)


def seed_hash(server_seed: str) -> str:
    return hashlib.sha256(server_seed.encode()).hexdigest()


def roll(server_seed: str, client_seed: str, nonce: int) -> int:
    """Число 0..999999, которое можно пересчитать после раскрытия server_seed."""
    digest = hmac.new(
        server_seed.encode(), f"{client_seed}:{nonce}".encode(), hashlib.sha256
    ).hexdigest()
    return int(digest[:8], 16) % MAX_NUMBER


def ranges(chance: int) -> tuple[tuple[int, int], tuple[int, int]]:
    t = chance * 10_000
    return (0, t - 1), (MAX_NUMBER - t, MAX_NUMBER - 1)


def is_win(number: int, chance: int, direction: str) -> bool:
    t = chance * 10_000
    if direction == "under":
        return number < t
    return number >= MAX_NUMBER - t


def multiplier(chance: int, rtp: float) -> float:
    return rtp / chance


def payout(bet: int, chance: int, rtp: float) -> int:
    """Полная выплата при выигрыше (включая возврат ставки): ставка * RTP / шанс."""
    return int(bet * rtp / chance)
