"""HTTP-сервер мини-аппа: отдаёт index.html и JSON API. Проверяет подпись Telegram."""
import os
from pathlib import Path

from aiohttp import web

import db
import engine

INDEX = Path(__file__).with_name("index.html")
# Только для локальной отладки без Telegram. На хостинге НЕ включать.
DEV_MODE = os.getenv("DEV_MODE") == "1"


def authed(handler):
    async def wrapper(request: web.Request) -> web.Response:
        user = engine.verify_init_data(
            request.headers.get("X-Init-Data", ""), request.app["token"]
        )
        if user is None and DEV_MODE:
            dev = request.headers.get("X-Dev-User", "")
            if dev.isdigit():
                user = {"id": int(dev), "first_name": f"Dev {dev}", "username": f"dev{dev}"}
        if user is None:
            return web.json_response({"error": "Открой игру из Telegram"}, status=401)
        db.get_user(user["id"], user.get("username"), user.get("first_name"))
        try:
            return web.json_response(await handler(request, user["id"]))
        except engine.GameError as e:
            return web.json_response({"error": str(e)}, status=400)

    return wrapper


async def body_of(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@authed
async def api_me(request, uid):
    return engine.me_payload(uid)


@authed
async def api_play(request, uid):
    b = await body_of(request)
    return engine.play(uid, b.get("bet"), b.get("chance"), b.get("direction"))


@authed
async def api_bonus(request, uid):
    return engine.claim_bonus(uid)


@authed
async def api_history(request, uid):
    scope = "mine" if request.query.get("scope") == "mine" else "all"
    return {"games": engine.history_payload(uid, scope)}


@authed
async def api_fair(request, uid):
    return engine.fair_payload(uid)


@authed
async def api_rotate(request, uid):
    b = await body_of(request)
    result = engine.rotate_seeds(uid, str(b.get("client_seed") or ""))
    result["fair"] = engine.fair_payload(uid)
    return result


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(INDEX, headers={"Cache-Control": "no-store"})


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app(bot_token: str) -> web.Application:
    app = web.Application()
    app["token"] = bot_token
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/play", api_play)
    app.router.add_post("/api/bonus", api_bonus)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/api/fair", api_fair)
    app.router.add_post("/api/rotate", api_rotate)
    return app
