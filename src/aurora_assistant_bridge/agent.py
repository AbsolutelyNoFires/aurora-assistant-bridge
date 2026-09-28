"""The assistant loop: one LLM call at a time, woken by chat, journal activity or a timer.

Context = system prompt + the chat transcript interleaved (by time) with the journal, each truncated
separately. Truncation happens in large steps so the prompt prefix stays stable between calls, which
lets local servers (llama.cpp etc.) reuse their KV cache.
"""

import asyncio
import json
import logging
import time

from .config import Config
from .hub import Hub
from .journal import format_entry
from .llm import LLM, LLMError, parse_text_actions
from .patch_client import PatchClient
from .store import Store
from .tools import TOOLS, TOOL_NAMES, run_tool, text_mode_instructions

log = logging.getLogger("agent")

SYSTEM_PROMPT = """You are an assistant to the player of Aurora 4X (C# edition), a deep 4X space strategy game. \
You and the player work in the same running game: you can see what the player does through the game journal, \
read any open window as text, and operate the game's windows yourself with tools. The player may be using the \
game at the same time as you.

How to work:
- The journal shows recent actions by the player, by you (assistant) and the game, oldest first, with in-game dates.
- Windows are read as an outline of controls. Use read_window before acting on a window, and use the exact control \
names it shows. Main windows open from toolbar buttons on the Tactical Map.
- After acting, check the result (the tool result, or read the window again). If a message box appears, read it \
and answer it with answer_dialog.
- Do what the player asks, carefully and completely. Ask before anything destructive or irreversible \
(deleting, scrapping, big spending) unless the player clearly asked for it.
- Keep replies short and concrete: what you did, what you found, what you suggest."""

WAKE_PROMPT = ("(No new message from the player. Game activity since you last spoke is in the journal above. "
               "If there is something genuinely useful to point out or do for the player right now, do it. "
               "Otherwise reply with exactly: NOTHING)")

OLD_TOOL_RESULT_CHARS = 600
# Conservative characters per token for budgeting (window text, JSON and numbers tokenize densely).
CHARS_PER_TOKEN = 3
# Tool results of the current task kept in full even when the task alone outgrows the budget.
KEEP_RECENT_RESULTS = 2


class Agent:
    def __init__(self, cfg: Config, store: Store, hub: Hub, patch: PatchClient):
        self.cfg = cfg
        self.store = store
        self.hub = hub
        self.patch = patch
        self.llm = None
        self.reconfigure()
        self.wake = asyncio.Event()
        self.busy = False
        self.current: asyncio.Task | None = None
        self.partial: list[str] = []  # Reply text streamed so far in the current call.
        self.clearing = False
        self.last_turn_time = time.time()
        # Ids of the last chat row / journal entry the companion has seen.
        tail = store.chat_tail(1)
        self.answered_chat_id = tail[-1]["id"] if tail else 0
        jtail = store.journal_tail(1)
        self.seen_journal_id = jtail[-1]["id"] if jtail else 0
        # Truncation windows (first included id), advanced in steps.
        self.chat_start = 0
        self.journal_start = 0
        self.squeezed: set[int] = set()  # current-task tool results shortened to fit the context

    # Triggers ------------------------------------------------------------------------------

    def player_message(self, text: str) -> dict:
        row = self.store.add_chat("user", text)
        self.hub.publish({"type": "chat", "message": row})
        self.wake.set()
        return row

    async def run(self):
        journal_q = self.hub.subscribe()
        asyncio.create_task(self._journal_watch(journal_q))
        asyncio.create_task(self._timer())
        while True:
            await self.wake.wait()
            self.wake.clear()
            reason = self._due()
            if reason is None:
                continue
            self.current = asyncio.create_task(self.turn(reason))
            try:
                await self.current
            except asyncio.CancelledError:
                if not self.current.cancelled():
                    raise  # The bridge itself is shutting down.
                # Stopped by the player. Everything up to now counts as handled, so the companion
                # does not immediately start again on the same message.
                self._mark_seen()
                if self.clearing:
                    self.clearing = False
                    continue
                if "".join(self.partial).strip():
                    self._post_assistant("".join(self.partial).strip() + " …")
                self._post_note("stopped")
            except Exception as e:
                log.exception("turn failed")
                self._post_assistant(f"(assistant error: {e})")
            finally:
                self.current = None
            if self._due():
                self.wake.set()

    def reconfigure(self):
        """(Re)create the model client from the current settings; the next call uses them."""
        cfg = self.cfg
        self.llm = LLM(cfg.llm_base_url, cfg.llm_model, cfg.llm_api_key, cfg.llm_temperature, cfg.llm_timeout_s,
                       cfg.llm_max_tokens) if cfg.llm_enabled else None

    def clear_history(self):
        """Stop any running turn and start the assistant's context afresh."""
        self.clearing = self.stop()
        self.store.clear()
        self.chat_start = self.journal_start = 0
        self._mark_seen()
        self.hub.publish({"type": "cleared"})

    def stop(self) -> bool:
        """Cancel the running turn. Cancelling closes the streaming request, which makes the model
        server (llama.cpp) abort generation; no further tool steps run."""
        if self.current and not self.current.done():
            self.current.cancel()
            return True
        return False

    def _due(self) -> str | None:
        if any(r["role"] == "user" for r in self.store.chat_since(self.answered_chat_id)):
            return "chat"
        new = len(self.store.journal_since(self.seen_journal_id))
        if self.cfg.wake_journal_entries and new >= self.cfg.wake_journal_entries:
            return "journal"
        if self.cfg.wake_minutes and new and time.time() - self.last_turn_time >= self.cfg.wake_minutes * 60:
            return "timer"
        return None

    async def _journal_watch(self, q: asyncio.Queue):
        while True:
            msg = await q.get()
            if msg["type"] == "journal" and not self.busy:
                self.wake.set()

    async def _timer(self):
        while True:
            await asyncio.sleep(30)
            self.wake.set()

    # A turn ---------------------------------------------------------------------------------

    async def turn(self, reason: str):
        self._set_busy(True)
        try:
            if not self.llm:
                if reason == "chat":
                    self._mark_seen()
                    self._post_assistant("(No LLM is configured yet: set LLM_BASE_URL for the bridge.)")
                else:
                    self._mark_seen()
                return

            text_mode = self.cfg.tool_mode == "text"
            nudge = None if reason == "chat" else WAKE_PROMPT
            for step in range(self.cfg.max_tool_steps):
                messages = _merge_consecutive_user(self.build_context(text_mode) + [await self._state_note(nudge)])
                self._mark_seen()
                self.hub.publish({"type": "thinking"})
                self.partial = []
                result = await self.llm.complete(
                    messages, None if text_mode else TOOLS,
                    on_delta=self._publish_delta, on_reasoning=self._publish_reasoning)
                content = result["content"].strip()
                calls = result["tool_calls"]
                if text_mode:
                    _, calls = parse_text_actions(content)

                if not calls:
                    if nudge and (not content or content.upper().startswith("NOTHING")):
                        self.hub.publish({"type": "discard"})
                        return
                    if nudge:
                        self._post_note(reason)
                    self._post_assistant(content)
                    return

                if nudge:
                    # The companion decided to act on its own; record why it woke, for its own history.
                    self._post_note(reason)
                    nudge = None
                row = self.store.add_chat("assistant", content or None, tool_calls=None if text_mode else calls)
                self.hub.publish({"type": "chat", "message": {**row, "tool_calls": calls}})
                for call in calls:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    output = await run_tool(self.patch, name, args) if name in TOOL_NAMES else f"error: unknown tool {name}"
                    trow = self.store.add_chat("tool", output, tool_call_id=call["id"], name=name)
                    self.hub.publish({"type": "chat", "message": {**trow, "args": args}})
                # Let the journal catch up with what the tools did before the next step.
                await asyncio.sleep(0.8)
            self._post_assistant(f"(Stopped after {self.cfg.max_tool_steps} steps.)")
        except LLMError as e:
            self._post_assistant(f"(LLM error: {e})")
        finally:
            self.last_turn_time = time.time()
            self._set_busy(False)

    # Context --------------------------------------------------------------------------------

    def budgets(self) -> tuple[int, int]:
        """(chat, journal) budgets in characters, from the model's context window unless set explicitly.
        Leaves room for the reply (including reasoning), the system prompt and the tool definitions."""
        cfg = self.cfg
        ctx = max(cfg.llm_context_tokens, 2048)
        reply = cfg.llm_max_tokens if cfg.llm_max_tokens > 0 else min(16384, ctx // 4)
        fixed = len(SYSTEM_PROMPT) + len(json.dumps(TOOLS)) + 3000  # + the state note
        available = max((ctx - reply) * CHARS_PER_TOKEN - fixed, 4000)
        return (cfg.chat_budget_chars or int(available * 0.7),
                cfg.journal_budget_chars or int(available * 0.3))

    def build_context(self, text_mode: bool) -> list[dict]:
        chat_budget, journal_budget = self.budgets()
        chat = self.store.chat_since(self.chat_start)
        journal = self.store.journal_since(self.journal_start)
        chat = self._truncate_chat(chat, chat_budget)
        journal = self._truncate_journal(journal, journal_budget)

        system = SYSTEM_PROMPT + ("\n\n" + text_mode_instructions() if text_mode else "")
        messages: list[dict] = [{"role": "system", "content": system}]
        current_turn_start = self._current_turn_start(chat)

        items = [("chat", r["time"], r) for r in chat] + [("journal", e["time"], e) for e in journal]
        items.sort(key=lambda i: (i[1], 0 if i[0] == "chat" else 1))

        pending_journal: list[str] = []

        def flush_journal():
            if pending_journal:
                messages.append({"role": "user", "content": "[Game journal]\n" + "\n".join(pending_journal)})
                pending_journal.clear()

        for kind, _, r in items:
            if kind == "journal":
                pending_journal.append(format_entry(r))
                continue
            # Journal lines must not split an assistant tool call from its tool results.
            in_tool_run = messages[-1]["role"] == "tool" or bool(messages[-1].get("tool_calls"))
            if r["role"] == "tool":
                content = r["content"] or ""
                if (r["id"] < current_turn_start or r["id"] in self.squeezed) and len(content) > OLD_TOOL_RESULT_CHARS:
                    content = content[:OLD_TOOL_RESULT_CHARS] + " …"
                if text_mode:
                    messages.append({"role": "user", "content": f"[result of {r['name']}]\n{content}"})
                else:
                    messages.append({"role": "tool", "tool_call_id": r["tool_call_id"], "content": content})
                continue
            if not in_tool_run or r["role"] == "user":
                flush_journal()
            if r["role"] == "user":
                messages.append({"role": "user", "content": f"[Player] {r['content']}"})
            elif r["role"] == "note":
                messages.append({"role": "user", "content": r["content"]})
            else:
                m = {"role": "assistant", "content": r["content"] or ""}
                if r["tool_calls"] and not text_mode:
                    m["tool_calls"] = r["tool_calls"]
                messages.append(m)
        flush_journal()
        return _merge_consecutive_user(messages)

    def _truncate_chat(self, rows: list[dict], budget: int) -> list[dict]:
        """Keep the chat within budget. Whole older turns are dropped from the front (in large steps, so the
        prompt prefix stays stable for the server's cache); the current turn is never dropped. If the current
        turn alone is too big, its older tool results are shortened instead."""
        turn_start = self._current_turn_start(rows)
        self.squeezed = set()

        def size(r):
            content = r.get("content") or ""
            if r["role"] == "tool" and r["id"] < turn_start:
                return min(len(content), OLD_TOOL_RESULT_CHARS + 2)  # as build_context will send it
            return len(content) + (len(json.dumps(r["tool_calls"])) if r.get("tool_calls") else 0)

        total = sum(size(r) for r in rows)
        if total > budget:
            target = budget * 0.7
            i = 0
            while i < len(rows) and total > target and rows[i]["id"] < turn_start:
                total -= size(rows[i])
                i += 1
            # Start at a turn boundary so no turn is cut in half.
            while i < len(rows) and rows[i]["id"] < turn_start and rows[i]["role"] not in ("user", "note"):
                total -= size(rows[i])
                i += 1
            if i:
                rows = rows[i:]
                self.chat_start = rows[0]["id"] - 1

        if total > budget:
            results = [r for r in rows if r["role"] == "tool" and r["id"] >= turn_start]
            for r in results[:-KEEP_RECENT_RESULTS] if len(results) > KEEP_RECENT_RESULTS else []:
                if total <= budget:
                    break
                content_len = len(r.get("content") or "")
                if content_len > OLD_TOOL_RESULT_CHARS:
                    self.squeezed.add(r["id"])
                    total -= content_len - OLD_TOOL_RESULT_CHARS
            if total > budget:
                log.warning("the current task needs ~%d tokens but the chat budget is ~%d (context window %d tokens); "
                            "raise the context window in Settings if the server allows it",
                            total // CHARS_PER_TOKEN, budget // CHARS_PER_TOKEN, self.cfg.llm_context_tokens)
        return rows

    def _truncate_journal(self, rows: list[dict], budget: int) -> list[dict]:
        size = sum(len(e["text"]) + 30 for e in rows)
        if size <= budget:
            return rows
        target = budget * 0.7
        while rows and size > target:
            size -= len(rows[0]["text"]) + 30
            rows = rows[1:]
        self.journal_start = rows[0]["id"] - 1 if rows else self.journal_start
        return rows

    @staticmethod
    def _current_turn_start(chat: list[dict]) -> int:
        for r in reversed(chat):
            if r["role"] in ("user", "note"):
                return r["id"]
        return 0

    async def _state_note(self, nudge: str | None) -> dict:
        """Transient last message: current game date and open windows (not stored, so the prefix stays stable)."""
        try:
            health = await self.patch.health()
            forms = await self.patch.forms()
            windows = ", ".join(f["name"] + (" (active)" if f["active"] else "")
                                for f in forms if f["type"] != "AuroraPatchForm")
            title = (health.get("title") or "").split("   ")
            state = f"[Now] {title[1].strip() if len(title) > 1 else '?'} — open windows: {windows}"
        except Exception as e:
            state = f"[Now] game not reachable: {e}"
        return {"role": "user", "content": state + ("\n\n" + nudge if nudge else "")}

    # Output helpers -------------------------------------------------------------------------

    def _mark_seen(self):
        tail = self.store.chat_tail(1)
        if tail:
            self.answered_chat_id = tail[-1]["id"]
        jt = self.store.journal_tail(1)
        if jt:
            self.seen_journal_id = jt[-1]["id"]

    def _post_assistant(self, text: str):
        row = self.store.add_chat("assistant", text)
        self.answered_chat_id = row["id"]
        self.hub.publish({"type": "chat", "message": row})

    def _post_note(self, reason: str):
        text = {"journal": "(Assistant checked in on recent game activity.)",
                "timer": "(Assistant checked in after a while.)",
                "stopped": "(Stopped by the player.)"}.get(reason, f"({reason})")
        row = self.store.add_chat("note", text)
        self.hub.publish({"type": "chat", "message": row})

    async def _publish_reasoning(self, d: str):
        self.hub.publish({"type": "reasoning", "text": d})

    async def _publish_delta(self, d: str):
        self.partial.append(d)
        self.hub.publish({"type": "delta", "text": d})

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.hub.publish({"type": "status", "busy": busy})


def _merge_consecutive_user(messages: list[dict]) -> list[dict]:
    """Some chat templates reject two user turns in a row."""
    out: list[dict] = []
    for m in messages:
        if out and m["role"] == "user" and out[-1]["role"] == "user":
            out[-1] = {"role": "user", "content": out[-1]["content"] + "\n\n" + m["content"]}
        else:
            out.append(m)
    return out
