"""Игровой бот «меньше/больше» на виртуальные очки (без реальных денег).

Честная игра: у каждого игрока пара сидов. Хэш серверного сида показан заранее,
сам сид раскрывается при смене пары, любой бросок можно пересчитать.
RTP, бонусы и выдача монет настраиваются админом командами в чате.
"""
import asyncio
import logging
import os
import time

from aiohttp import web as aioweb
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from dotenv import load_dotenv

import db
import game
import web

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
CUR = "🪙"  # игровая валюта, не имеет денежной стоимости
WEBAPP_URL = os.getenv("WEBAPP_URL", "").rstrip("/")

router = Router()


def money(units: int) -> str:
    return f"{units / 100:,.2f}".replace(",", " ")


def parse_amount(text: str) -> int | None:
    try:
        value = float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None
    if value <= 0 or value > 1e9:
        return None
    return int(round(value * 100))


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ---------- главный экран ----------

def render(uid: int, note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    u = db.get_user(uid)
    rtp = float(db.get_setting("rtp"))
    chance, bet = u["chance"], u["bet"]
    under, over = game.ranges(chance)
    win_amount = game.payout(bet, chance, rtp)
    text = (
        (note + "\n\n" if note else "")
        + f"💰 Баланс: <b>{money(u['balance'])}</b> {CUR}\n\n"
        + f"Возможный выигрыш: <b>{money(win_amount)}</b> {CUR}  (×{game.multiplier(chance, rtp):.2f})\n"
        + f"Ставка: <b>{money(bet)}</b> {CUR}   Шанс: <b>{chance}%</b>\n\n"
        + f"⬇️ Меньше: {under[0]} – {under[1]}\n"
        + f"⬆️ Больше: {over[0]} – {over[1]}\n\n"
        + "<i>Сумму можно отправить сообщением, например: 5 или 0.25</i>"
    )
    b = InlineKeyboardButton
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [b(text="½", callback_data="b:half"), b(text="×2", callback_data="b:dbl"), b(text="MAX", callback_data="b:max")],
            [
                b(text="−10", callback_data="c:-10"),
                b(text="−1", callback_data="c:-1"),
                b(text="+1", callback_data="c:1"),
                b(text="+10", callback_data="c:10"),
            ],
            [b(text="⬇️ Меньше", callback_data="g:under"), b(text="⬆️ Больше", callback_data="g:over")],
            [b(text="👤 Профиль", callback_data="p"), b(text="🔒 Честная игра", callback_data="f")],
            [b(text="📜 Игры", callback_data="h"), b(text="🎁 Бонус", callback_data="d")],
        ]
    )
    return text, kb


async def show(target: Message | CallbackQuery, uid: int, note: str = "") -> None:
    text, kb = render(uid, note)
    msg = target.message if isinstance(target, CallbackQuery) else None
    try:
        if msg:
            await msg.edit_text(text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


# ---------- команды ----------

@router.message(Command("start"))
async def start(m: Message) -> None:
    db.get_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    if WEBAPP_URL:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]
            ]
        )
        await m.answer(
            f"Привет! Это игра на виртуальные очки {CUR}. Реальных денег здесь нет: "
            "очки нельзя ни купить, ни вывести.\n\nНажми «Играть».",
            reply_markup=kb,
        )
    else:
        await m.answer(
            f"Привет! Это игра на виртуальные очки {CUR}. Реальных денег здесь нет: "
            "очки нельзя ни купить, ни вывести."
        )
        await show(m, m.from_user.id)


@router.message(Command("classic"))
async def classic(m: Message) -> None:
    db.get_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    await show(m, m.from_user.id)


@router.message(Command("myid"))
async def myid(m: Message) -> None:
    await m.answer(f"Твой Telegram ID: <code>{m.from_user.id}</code>")


@router.message(Command("seed"))
async def set_seed(m: Message, command: CommandObject) -> None:
    if not command.args:
        await m.answer("Использование: /seed твой_текст (это сменит пару сидов)")
        return
    await rotate(m.from_user.id, command.args.strip()[:32], m)


@router.message(F.text.regexp(r"^\s*\d+([.,]\d{1,2})?\s*$"))
async def set_bet(m: Message) -> None:
    u = db.get_user(m.from_user.id, m.from_user.username)
    amount = parse_amount(m.text)
    min_bet = int(db.get_setting("min_bet"))
    if amount is None or amount < min_bet:
        await m.answer(f"Минимальная ставка: {money(min_bet)} {CUR}")
        return
    db.update(u["id"], bet=min(amount, max(u["balance"], min_bet)))
    await show(m, m.from_user.id)


# ---------- кнопки ----------

@router.callback_query(F.data.startswith("b:"))
async def bet_buttons(c: CallbackQuery) -> None:
    u = db.get_user(c.from_user.id, c.from_user.username)
    min_bet = int(db.get_setting("min_bet"))
    action = c.data[2:]
    bet = {"half": u["bet"] // 2, "dbl": u["bet"] * 2, "max": u["balance"]}[action]
    bet = max(min_bet, min(bet, max(u["balance"], min_bet)))
    db.update(u["id"], bet=bet)
    await show(c, c.from_user.id)
    await c.answer()


@router.callback_query(F.data.startswith("c:"))
async def chance_buttons(c: CallbackQuery) -> None:
    u = db.get_user(c.from_user.id, c.from_user.username)
    chance = max(game.MIN_CHANCE, min(game.MAX_CHANCE, u["chance"] + int(c.data[2:])))
    db.update(u["id"], chance=chance)
    await show(c, c.from_user.id)
    await c.answer()


@router.callback_query(F.data.startswith("g:"))
async def play(c: CallbackQuery) -> None:
    direction = c.data[2:]
    u = db.get_user(c.from_user.id, c.from_user.username)
    bet, chance = u["bet"], u["chance"]
    if bet > u["balance"] or u["balance"] <= 0:
        await c.answer("Недостаточно монет", show_alert=True)
        return
    rtp = float(db.get_setting("rtp"))
    number = game.roll(u["server_seed"], u["client_seed"], u["nonce"])
    win = game.is_win(number, chance, direction)
    pay = game.payout(bet, chance, rtp) if win else 0
    db.add_balance(u["id"], pay - bet)
    db.add_game(u["id"], bet, chance, direction, number, win, pay, u["nonce"])
    db.update(u["id"], nonce=u["nonce"] + 1)
    arrow = "⬇️ Меньше" if direction == "under" else "⬆️ Больше"
    if win:
        note = f"🎲 Выпало <b>{number}</b> ({arrow}) ✅ <b>+{money(pay - bet)}</b> {CUR}"
    else:
        note = f"🎲 Выпало <b>{number}</b> ({arrow}) ❌ <b>−{money(bet)}</b> {CUR}"
    await show(c, c.from_user.id, note)
    await c.answer()


@router.callback_query(F.data == "d")
async def daily(c: CallbackQuery) -> None:
    u = db.get_user(c.from_user.id, c.from_user.username)
    left = u["last_bonus"] + 86400 - int(time.time())
    if left > 0:
        await c.answer(f"Следующий бонус через {left // 3600} ч {left % 3600 // 60} мин", show_alert=True)
        return
    bonus = int(db.get_setting("daily_bonus"))
    db.add_balance(u["id"], bonus)
    db.update(u["id"], last_bonus=int(time.time()))
    await show(c, c.from_user.id, f"🎁 Ежедневный бонус: <b>+{money(bonus)}</b> {CUR}")
    await c.answer()


@router.callback_query(F.data == "p")
async def profile(c: CallbackQuery) -> None:
    u = db.get_user(c.from_user.id, c.from_user.username)
    s = db.user_stats(u["id"])
    text = (
        f"👤 <b>Профиль</b>\n@{u['username'] or '—'}, в игре с "
        f"{time.strftime('%d.%m.%Y', time.localtime(u['joined']))}\n\n"
        f"Баланс: <b>{money(u['balance'])}</b> {CUR}\n"
        f"Ставок: <b>{s['n']}</b>\nОборот: <b>{money(s['turnover'])}</b>\n"
        f"Выплачено: <b>{money(s['paid'])}</b>\nЛучший выигрыш: <b>+{money(s['best'])}</b>\n\n"
        f"<i>{CUR} — игровая валюта без денежной стоимости.</i>"
    )
    await back_screen(c, text)


@router.callback_query(F.data == "h")
async def history(c: CallbackQuery) -> None:
    rows = db.last_games(c.from_user.id, 10)
    lines = ["📜 <b>Последние игры</b>\n"]
    for r in rows:
        arrow = "⬇️" if r["direction"] == "under" else "⬆️"
        res = f"+{money(r['payout'] - r['bet'])}" if r["win"] else f"−{money(r['bet'])}"
        lines.append(f"{arrow} шанс {r['chance']}% · выпало {r['number']} · <b>{res}</b>")
    if not rows:
        lines.append("Пока пусто.")
    await back_screen(c, "\n".join(lines))


@router.callback_query(F.data == "f")
async def fair(c: CallbackQuery) -> None:
    u = db.get_user(c.from_user.id, c.from_user.username)
    text = (
        "🔒 <b>Честная игра</b>\n\n"
        "Число каждой ставки определено до неё. Хэш серверного сида показан заранее, "
        "сам сид раскрывается после смены пары. Число = первые 8 hex-символов "
        "HMAC-SHA256(server_seed, «client_seed:nonce») по модулю 1 000 000.\n\n"
        "«Меньше» выигрывает, если число &lt; шанс × 10 000, «Больше» — если число ≥ "
        "1 000 000 − шанс × 10 000. Выплата = ставка × RTP / шанс.\n\n"
        f"Хэш серверного сида:\n<code>{game.seed_hash(u['server_seed'])}</code>\n\n"
        f"Клиентский сид: <code>{u['client_seed']}</code>\n"
        f"Ставок на этой паре (nonce): <b>{u['nonce']}</b>\n\n"
        f"Хэш СЛЕДУЮЩЕГО серверного сида:\n<code>{game.seed_hash(u['next_seed'])}</code>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сменить пару сидов", callback_data="rot")],
            [InlineKeyboardButton(text="« Назад", callback_data="home")],
        ]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


@router.callback_query(F.data == "rot")
async def rotate_cb(c: CallbackQuery) -> None:
    await rotate(c.from_user.id, None, c.message)
    await c.answer()


async def rotate(uid: int, client_seed: str | None, msg: Message) -> None:
    u = db.get_user(uid)
    db.update(
        uid,
        server_seed=u["next_seed"],
        next_seed=game.new_server_seed(),
        client_seed=client_seed or game.new_client_seed(),
        nonce=0,
    )
    await msg.answer(
        "🔓 Пара сидов сменена. Раскрытый серверный сид прошлой пары:\n"
        f"<code>{u['server_seed']}</code>\n"
        f"Клиентский сид: <code>{u['client_seed']}</code>, бросков: {u['nonce']}.\n"
        "Проверь любой бросок формулой из раздела «Честная игра»."
    )


@router.callback_query(F.data == "home")
async def home(c: CallbackQuery) -> None:
    await show(c, c.from_user.id)
    await c.answer()


async def back_screen(c: CallbackQuery, text: str) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="« Назад", callback_data="home")]]
    )
    await c.message.edit_text(text, reply_markup=kb)
    await c.answer()


# ---------- админка (только ADMIN_IDS) ----------

@router.message(Command("admin"))
async def admin_help(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    await m.answer(
        "<b>Админ-команды</b>\n"
        "/setrtp 97 — RTP в процентах (1–100)\n"
        "/give &lt;id|@username&gt; 100 — выдать монеты\n"
        "/take &lt;id|@username&gt; 100 — забрать монеты\n"
        "/setbonus 10 — ежедневный бонус\n"
        "/setstart 1000 — стартовый баланс новых игроков\n"
        "/stats — статистика\n"
        f"Сейчас: RTP {db.get_setting('rtp')}%"
    )


@router.message(Command("setrtp"))
async def setrtp(m: Message, command: CommandObject) -> None:
    if not is_admin(m.from_user.id):
        return
    try:
        value = float((command.args or "").replace(",", "."))
        assert 1 <= value <= 100
    except (ValueError, AssertionError):
        await m.answer("Использование: /setrtp 97 (число от 1 до 100)")
        return
    db.set_setting("rtp", str(value))
    await m.answer(f"RTP установлен: {value}%")


@router.message(Command("setbonus"))
async def setbonus(m: Message, command: CommandObject) -> None:
    await _set_amount(m, command, "daily_bonus", "Ежедневный бонус")


@router.message(Command("setstart"))
async def setstart(m: Message, command: CommandObject) -> None:
    await _set_amount(m, command, "start_balance", "Стартовый баланс")


async def _set_amount(m: Message, command: CommandObject, key: str, label: str) -> None:
    if not is_admin(m.from_user.id):
        return
    amount = parse_amount(command.args or "")
    if amount is None:
        await m.answer("Укажи сумму, например: 10")
        return
    db.set_setting(key, str(amount))
    await m.answer(f"{label}: {money(amount)} {CUR}")


@router.message(Command("give", "take"))
async def give(m: Message, command: CommandObject) -> None:
    if not is_admin(m.from_user.id):
        return
    parts = (command.args or "").split()
    amount = parse_amount(parts[1]) if len(parts) == 2 else None
    user = db.find_user(parts[0]) if parts else None
    if amount is None or user is None:
        await m.answer(f"Использование: /{command.command} &lt;id|@username&gt; сумма (игрок должен был нажать /start)")
        return
    delta = amount if command.command == "give" else -min(amount, user["balance"])
    db.add_balance(user["id"], delta)
    new = db.get_user(user["id"])
    await m.answer(f"Готово. Баланс {user['id']}: {money(new['balance'])} {CUR}")


@router.message(Command("stats"))
async def stats(m: Message) -> None:
    if not is_admin(m.from_user.id):
        return
    s = db.global_stats()
    held = s["turnover"] - s["paid"]
    await m.answer(
        f"Игроков: {s['users']}\nИгр: {s['n']}\nОборот: {money(s['turnover'])}\n"
        f"Выплачено: {money(s['paid'])}\nОстаток у «банка»: {money(held)}\n"
        f"Текущий RTP настройки: {db.get_setting('rtp')}%"
    )


async def main() -> None:
    if not TOKEN:
        raise SystemExit("Нет BOT_TOKEN. Создай файл .env по образцу .env.example")
    logging.basicConfig(level=logging.INFO)
    db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    # веб-сервер мини-аппа (Railway сам задаёт PORT)
    runner = aioweb.AppRunner(web.create_app(TOKEN))
    await runner.setup()
    await aioweb.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080"))).start()

    if WEBAPP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
