"""Minimal streaming client for OpenAI-style /v1/chat/completions, with tool calls."""

import json
import logging
import re
import time
from typing import Awaitable, Callable

import aiohttp


log = logging.getLogger("llm")


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str, temperature: float, timeout_s: int, max_tokens: int = 0):
        self.url = api_url(base_url, "chat/completions")
        self.max_tokens = max_tokens
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = aiohttp.ClientTimeout(total=timeout_s, sock_read=timeout_s)

    async def complete(self, messages: list[dict], tools: list[dict] | None,
                       on_delta: Callable[[str], Awaitable[None]] | None = None,
                       on_reasoning: Callable[[str], Awaitable[None]] | None = None) -> dict:
        """Stream one completion. Returns {"content": str, "tool_calls": [...]}."""
        body = {"model": self.model, "messages": messages, "stream": True, "temperature": self.temperature}
        if tools:
            body["tools"] = tools
        if self.max_tokens > 0:
            body["max_tokens"] = self.max_tokens
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        content = []
        reasoning_chars = 0
        timings = None
        started = time.monotonic()
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
                    timings = chunk.get("timings") or timings  # llama.cpp reports these on the last chunk
                    if not chunk.get("choices"):
                        continue
                    delta = chunk["choices"][0].get("delta") or {}
                    # Reasoning models (Qwen3, DeepSeek...) stream their thinking separately; it is shown
                    # live but not kept in the history.
                    if delta.get("reasoning_content"):
                        reasoning_chars += len(delta["reasoning_content"])
                        if on_reasoning:
                            await on_reasoning(delta["reasoning_content"])
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

        elapsed = time.monotonic() - started
        if timings:
            log.info("call %.1fs: prompt %d tok (%d cached) in %.1fs @ %.0f tok/s; generated %d tok in %.1fs @ %.1f tok/s "
                     "(reasoning %d chars, reply %d chars)", elapsed,
                     timings.get("prompt_n", 0) + timings.get("cache_n", 0), timings.get("cache_n", 0),
                     timings.get("prompt_ms", 0) / 1000, timings.get("prompt_per_second", 0),
                     timings.get("predicted_n", 0), timings.get("predicted_ms", 0) / 1000,
                     timings.get("predicted_per_second", 0), reasoning_chars, sum(map(len, content)))
        else:
            log.info("call %.1fs (reasoning %d chars, reply %d chars)", elapsed, reasoning_chars, sum(map(len, content)))

        tool_calls = [
            {"id": c["id"] or f"call_{i}", "type": "function",
             "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
            for i, c in sorted(calls.items())
        ]
        return {"content": "".join(content), "tool_calls": tool_calls}


def api_url(base_url: str, path: str) -> str:
    """The server's /v1/<path>; the base URL may be the server root or end in /v1."""
    base = base_url.rstrip("/")
    return f"{base}/{path}" if base.endswith("/v1") else f"{base}/v1/{path}"


async def list_models(base_url: str, api_key: str) -> list[str]:
    """Model ids from the server's /v1/models; also serves as a connection test."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.get(api_url(base_url, "models"), headers=headers) as r:
            if r.status >= 400:
                raise LLMError(f"HTTP {r.status}: {(await r.text())[:300]}")
            data = await r.json(content_type=None)
    return sorted(m["id"] for m in data.get("data", []) if "id" in m)


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
