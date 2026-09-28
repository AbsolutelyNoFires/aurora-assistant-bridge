#!/usr/bin/env python3
"""Scripted OpenAI-style /v1/chat/completions server for testing the bridge without a real model.

For a player message it lists windows, opens Class Design, reads it, and then replies with a summary.
For a background wake-up it answers NOTHING. Responses stream, with tool-call arguments split across
chunks like real servers do.
"""

import json
import sys

from aiohttp import web

SCRIPT = [
    ("list_windows", {}),
    ("click", {"window": "TacticalMapForm", "control": "cmdToolbarClass"}),
    ("read_window", {"window": "Class Design"}),
]


def chunk(delta: dict, finish=None) -> bytes:
    return f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})}\n\n".encode()


async def completions(request: web.Request):
    body = await request.json()
    messages = body["messages"]
    last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
    wake = "reply with exactly: NOTHING" in messages[last_user]["content"]
    tool_results = [m for m in messages[last_user:] if m["role"] == "tool"]
    # Tool results come after the last player message; count them across the turn.
    player_idx = max((i for i, m in enumerate(messages) if m["role"] == "user" and "[Player]" in m["content"]), default=0)
    step = sum(1 for m in messages[player_idx:] if m["role"] == "tool")

    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    if wake:
        await resp.write(chunk({"role": "assistant", "content": "NOTHING"}, "stop"))
    elif step < len(SCRIPT):
        name, args = SCRIPT[step]
        text = json.dumps(args)
        await resp.write(chunk({"role": "assistant", "tool_calls": [
            {"index": 0, "id": f"call_{step}", "type": "function", "function": {"name": name, "arguments": text[: len(text) // 2]}}]}))
        await resp.write(chunk({"tool_calls": [{"index": 0, "function": {"arguments": text[len(text) // 2:]}}]}, "tool_calls"))
    else:
        last = [m for m in messages if m["role"] == "tool"][-1]["content"]
        summary = next((l.strip() for l in last.splitlines() if "tvClassList" in l), "no class list")
        for word in f"I opened Class Design for you. The class list is there ({summary}).".split(" "):
            await resp.write(chunk({"content": word + " "}))
        await resp.write(chunk({}, "stop"))
    await resp.write(b"data: [DONE]\n\n")
    return resp


async def models(request: web.Request):
    return web.json_response({"object": "list", "data": [{"id": "mock-model", "object": "model"}]})


async def props(request: web.Request):
    # llama.cpp reports its context size here.
    return web.json_response({"default_generation_settings": {"n_ctx": 262144}})


app = web.Application()
app.router.add_post("/v1/chat/completions", completions)
app.router.add_get("/v1/models", models)
app.router.add_get("/props", props)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 11999)
