"""Web chat: the page, its API, and a server-sent event stream of chat, journal and status updates."""

import asyncio
import json
from pathlib import Path

from aiohttp import web

from . import __version__
from .agent import Agent
from .config import UI_SETTINGS, Config, _coerce, config_path, env_overridden, save_settings
from .llm import LLMError, list_models
from .hub import Hub
from .store import Store

STATIC = Path(__file__).parent / "static"


def make_app(store: Store, hub: Hub, agent: Agent, cfg: Config) -> web.Application:
    app = web.Application()

    async def config(request: web.Request):
        return web.json_response({"app": "aurora-assistant-bridge", "version": __version__,
                                  "llm": cfg.llm_model if cfg.llm_enabled else None})

    async def index(request: web.Request):
        return web.FileResponse(STATIC / "index.html")

    def settings_view() -> dict:
        view = {k: getattr(cfg, k) for k in UI_SETTINGS if k != "llm_api_key"}
        view["has_api_key"] = bool(cfg.llm_api_key)
        view["env_overridden"] = [k for k in UI_SETTINGS if env_overridden(k)]
        view["config_file"] = str(config_path())
        return view

    async def get_settings(request: web.Request):
        return web.json_response(settings_view())

    async def put_settings(request: web.Request):
        body = await request.json()
        updates = {}
        try:
            for key in UI_SETTINGS:
                if key not in body or body[key] is None:
                    continue
                if key == "llm_api_key" and body[key] == "" and not body.get("clear_api_key"):
                    continue  # Blank key field means "keep the current key".
                updates[key] = _coerce(getattr(cfg, key), body[key])
        except ValueError as e:
            return web.json_response({"error": f"invalid value: {e}"}, status=400)
        if "llm_temperature" in updates and not 0 <= updates["llm_temperature"] <= 2:
            return web.json_response({"error": "temperature must be between 0 and 2"}, status=400)
        if "llm_max_tokens" in updates and updates["llm_max_tokens"] < 0:
            return web.json_response({"error": "max tokens must be 0 (server default) or more"}, status=400)
        for key, value in updates.items():
            setattr(cfg, key, value)
        save_settings(updates)
        agent.reconfigure()
        hub.publish({"type": "settings", "llm": cfg.llm_model if cfg.llm_enabled else None})
        return web.json_response(settings_view())

    async def models(request: web.Request):
        base = request.query.get("base_url") or cfg.llm_base_url
        key = request.query.get("api_key") or cfg.llm_api_key
        if not base:
            return web.json_response({"error": "no endpoint set"}, status=400)
        try:
            return web.json_response({"models": await list_models(base, key)})
        except TimeoutError:
            return web.json_response({"error": "could not reach the server (timed out)"}, status=502)
        except (LLMError, OSError, ValueError) as e:
            return web.json_response({"error": str(e) or type(e).__name__}, status=502)

    async def history(request: web.Request):
        return web.json_response({
            "chat": store.chat_tail(200),
            "journal": store.journal_tail(300),
            "busy": agent.busy,
        })

    async def chat(request: web.Request):
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "empty message"}, status=400)
        return web.json_response(agent.player_message(text))

    async def stop(request: web.Request):
        return web.json_response({"stopped": agent.stop()})

    async def clear(request: web.Request):
        agent.clear_history()
        return web.json_response({"ok": True})

    async def stream(request: web.Request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await resp.prepare(request)
        q = hub.subscribe()
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    await resp.write(f"data: {json.dumps(msg)}\n\n".encode())
                except asyncio.TimeoutError:
                    await resp.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            hub.unsubscribe(q)
        return resp

    app.router.add_get("/", index)
    app.router.add_get("/api/config", config)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", put_settings)
    app.router.add_get("/api/models", models)
    app.router.add_get("/api/history", history)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/stop", stop)
    app.router.add_post("/api/clear", clear)
    app.router.add_get("/api/stream", stream)
    return app
