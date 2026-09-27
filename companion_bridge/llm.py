"""Minimal streaming client for OpenAI-style /v1/chat/completions, with tool calls."""

import json
import re
from typing import Awaitable, Callable

import aiohttp


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str, temperature: float, timeout_s: int):
        self.url = base_url + ("/chat/completions" if base_url.endswith("/v1") else "/v1/chat/completions")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = aiohttp.ClientTimeout(total=timeout_s, sock_read=timeout_s)

    async def complete(self, messages: list[dict], tools: list[dict] | None,
                       on_delta: Callable[[str], Awaitable[None]] | None = None) -> dict:
        """Stream one completion. Returns {"content": str, "tool_calls": [...]}."""
        body = {"model": self.model, "messages": messages, "stream": True, "temperature": self.temperature}
        if tools:
            body["tools"] = tools
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        content = []
        calls: dict[int, dict] = {}
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(self.url, json=body, headers=headers) as r:
                if r.status >= 400:
                    raise LLMError(f"HTTP {r.status}: {(await r.text())[:500]}")
                async for raw in r.content:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if not chunk.get("choices"):
                        continue
                    delta = chunk["choices"][0].get("delta") or {}
                    if delta.get("content"):
                        content.append(delta["content"])
                        if on_delta:
                            await on_delta(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

        tool_calls = [
            {"id": c["id"] or f"call_{i}", "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
            for i, c in sorted(calls.items())
        ]
        return {"content": "".join(content), "tool_calls": tool_calls}


ACTION_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_text_actions(content: str) -> tuple[str, list[dict]]:
    """For TOOL_MODE=text: pull ```json {"tool": ..., "args": {...}}``` blocks out of a reply."""
    calls = []
    for i, m in enumerate(ACTION_RE.finditer(content)):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "tool" in obj:
            calls.append({"id": f"text_{i}", "type": "function",
                          "function": {"name": obj["tool"], "arguments": json.dumps(obj.get("args", {}))}})
    text = ACTION_RE.sub("", content).strip() if calls else content
    return text, calls
