"""Web UI: the shared game desktop (noVNC) beside the companion chat, plus an SSE update stream."""

import asyncio
import json
from pathlib import Path

import aiohttp
from aiohttp import web

from .agent import Agent
from .hub import Hub
from .store import Store

STATIC = Path(__file__).parent / "static"
NOVNC = Path("/usr/share/novnc")


def make_app(store: Store, hub: Hub, agent: Agent, vnc_ws_url: str) -> web.Application:
    app = web.Application()

    async def index(request: web.Request):
        return web.FileResponse(STATIC / "index.html")

    async def websockify(request: web.Request):
        """Proxy noVNC's websocket to websockify, so the whole UI is served from one port."""
        client = web.WebSocketResponse(protocols=("binary",), max_msg_size=0)
        await client.prepare(request)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(vnc_ws_url, protocols=("binary",), max_msg_size=0) as upstream:
                async def pump(src, dst):
                    async for msg in src:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            await dst.send_bytes(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            await dst.send_str(msg.data)
                        else:
                            break
                    await dst.close()
                await asyncio.gather(pump(client, upstream), pump(upstream, client), return_exceptions=True)
        return client

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
    app.router.add_get("/websockify", websockify)
    app.router.add_get("/novnc/websockify", websockify)  # noVNC resolves its path relative to vnc.html
    app.router.add_static("/novnc/", NOVNC, follow_symlinks=True)
    app.router.add_get("/api/history", history)
    app.router.add_post("/api/chat", chat)
    app.router.add_post("/api/stop", stop)
    app.router.add_get("/api/stream", stream)
    return app
