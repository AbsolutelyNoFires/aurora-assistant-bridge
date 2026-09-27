"""Turns the patch's raw UI event stream into a journal of salient, readable lines.

Generic events become lines like `Class Design: player ticked "Tanker"`. Per-window extractors add
what matters about the result, e.g. how a ship class design's summary changed after an edit.
"""

import asyncio
import logging
import re

from .hub import Hub
from .patch_client import PatchClient, PatchError
from .store import Store

log = logging.getLogger("journal")

ACTORS = {"user": "player", "api": "companion", "game": "game"}
PREFIXES = ("lstv", "cmd", "txt", "cbo", "chk", "rdo", "lst", "lv", "tv", "tab", "lbl", "flp", "pnl", "opt", "num")


def humanize(control: str | None) -> str:
    """tvClassList -> 'class list'."""
    if not control:
        return "?"
    name = control
    for p in PREFIXES:
        if name.startswith(p) and len(name) > len(p) and name[len(p)].isupper():
            name = name[len(p):]
            break
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()


def window_name(ev: dict) -> str:
    if ev.get("form") == "TacticalMap":
        return "Tactical Map"
    return ev.get("formTitle") or ev.get("form") or "?"


def describe(ev: dict) -> str | None:
    """One journal line (without actor/window) for a UI event, or None to skip it."""
    t = ev["type"]
    label = ev.get("label")
    what = f'"{label}"' if label else humanize(ev.get("control"))
    value = ev.get("value")

    if t == "click":
        return f"clicked {what}"
    if t == "check":
        if value == "selected":
            return f"chose {what}"
        if value in ("checked", "unchecked"):
            return f"{'ticked' if value == 'checked' else 'unticked'} {what}"
        return f"{value} in {what}"
    if t == "doubleclick":
        shown = (value or "").replace("\\", " > ")
        return f'double-clicked "{shown}" in {what}' if shown else f"double-clicked {what}"
    if t == "select":
        shown = (value or "").replace("\\", " > ")
        return f'selected "{shown}" in {what}'
    if t == "text":
        was = ev.get("from")
        return f'set {what} to "{value}"' + (f' (was "{was}")' if was is not None else "")
    if t == "tab":
        return f'switched to tab "{value}"'
    if t == "form_open":
        return "window opened"
    if t == "form_close":
        return "window closed"
    if t == "form_focus":
        return "switched to window"
    if t == "dialog_open":
        buttons = f" [{label}]" if label else ""
        return f'message box: "{value}"{buttons}'
    return None


class ClassDesignExtractor:
    """Reports what a class design looks like when viewed, and how its summary changes after edits."""

    WINDOW = "Class Design"
    MAX_DIFF_LINES = 6

    def __init__(self):
        self.last_class: str | None = None
        self.last_lines: list[str] = []

    async def after(self, patch: PatchClient, events: list[dict]) -> list[str]:
        if not any(e.get("formTitle") == self.WINDOW and e["type"] not in ("form_close", "form_focus") for e in events):
            return []
        try:
            tree = await patch.form_json(self.WINDOW, max=0)
        except PatchError:
            return []
        summary = _find_value(tree, "txtSummary")
        if not summary:
            return []
        lines = [re.sub(r"\s{2,}", "  ", l).strip() for l in summary.replace("\r\n", "\n").split("\n")]
        lines = [l for l in lines if l]
        class_name = lines[0].split(" class ")[0].strip() if lines else None

        out = []
        if class_name != self.last_class:
            out.append(f'viewing class "{class_name}": ' + " | ".join(lines[:2]))
        elif lines != self.last_lines:
            removed = [l for l in self.last_lines if l not in lines]
            added = [l for l in lines if l not in self.last_lines]
            parts = [f"- {l}" for l in removed[: self.MAX_DIFF_LINES]] + [f"+ {l}" for l in added[: self.MAX_DIFF_LINES]]
            if parts:
                out.append(f'class "{class_name}" design changed:\n' + "\n".join(parts))
        self.last_class, self.last_lines = class_name, lines
        return out


class ChangeExtractor:
    """For any window: after an action, report which controls changed value (e.g. a research ETA,
    a population's stockpile) so the journal records what the player saw as a result."""

    SKIP_WINDOWS = {"Tactical Map", "Class Design", "AuroraPatch"}
    MAX_LINES = 8
    MAX_VALUE = 140
    WINDOW = "*"

    def __init__(self):
        self.snapshots: dict[str, dict[str, str]] = {}

    async def after(self, patch: PatchClient, events: list[dict]) -> list[str]:
        out = []
        for window in dict.fromkeys(window_name(e) for e in events if not e["type"].startswith("dialog")):
            if window in self.SKIP_WINDOWS:
                continue
            if any(e["type"] == "form_close" and window_name(e) == window for e in events):
                self.snapshots.pop(window, None)
                continue
            try:
                tree = await patch.form_json(window)
            except PatchError:
                continue
            state = {}
            _flatten(tree, state)
            before = self.snapshots.get(window)
            self.snapshots[window] = state
            if before is None:
                continue  # First look at this window: just a baseline.
            # The controls the player touched are already described by their own events.
            touched = {e.get("control") for e in events if window_name(e) == window}
            changes = []
            for key, value in state.items():
                name = key.split("|", 1)[0]
                old = before.get(key)
                # Controls the player touched are described by their own events; controls that only just
                # appeared have no "before" worth reporting.
                if name in touched or old is None or old == value:
                    continue
                label = key.split("|", 1)[1] or humanize(name)
                if re.match(r"\d+ rows", value):
                    changes.append(f"{label} list: {value[: self.MAX_VALUE]}")  # the new contents are what matter
                else:
                    changes.append(f"{label}: {_clip(old, self.MAX_VALUE)} → {_clip(value, self.MAX_VALUE)}")
            if changes:
                more = f" (+{len(changes) - self.MAX_LINES} more)" if len(changes) > self.MAX_LINES else ""
                out.append(f"{window}: now showing " + "; ".join(changes[: self.MAX_LINES]) + more)
        return out


def _flatten(node: dict, state: dict, raw: bool = False):
    """control name|label -> value string, for controls that carry information.
    Multi-line values are joined into one line unless raw."""
    name = node.get("name")
    kind = node.get("kind")
    value = None
    if kind in ("TextBox", "RichTextBox", "ComboBox", "NumericUpDown"):
        value = node.get("value")
    elif kind == "Label" and ((name or "").startswith("txt") or node.get("label")):
        value = node.get("text")
    elif kind in ("CheckBox", "RadioButton"):
        value = "on" if node.get("checked") else "off"
    elif kind == "TabControl":
        value = node.get("selectedTab")
    elif kind == "TreeView":
        value = (node.get("selected") or "").replace("\\", " > ") or None
    elif kind == "ListBox":
        items = node.get("items") or []
        value = ", ".join(items[i] for i in node.get("selectedIndices", []) if i < len(items)) or None
    elif kind == "ListView":
        # Aurora draws headers as the first row(s); summarise the rest by their first column.
        rows = [r for r in node.get("rows", []) if any(c.strip() for c in r)]
        if node.get("columns") and all(c in ("", "ColumnHeader") for c in node["columns"]) and rows:
            rows = rows[1:]
        names = [r[0] if r[0].strip() or len(r) < 2 else r[1] for r in rows[:3]]
        value = f'{node.get("rowCount", 0)} rows' + (": " + ", ".join(names) + (", …" if len(rows) > 3 else "") if names else "")
    if name and value is not None:
        v = str(value) if raw else " / ".join(l.strip() for l in str(value).splitlines() if l.strip())
        state[f'{name}|{node.get("label") or ""}'] = v
    for child in node.get("children", []):
        _flatten(child, state, raw)


def _clip(v, n: int) -> str:
    v = "" if v is None else str(v)
    return '"' + (v if len(v) <= n else v[: n - 1] + "…") + '"'


def _find_value(node: dict, name: str):
    if node.get("name") == name:
        return node.get("value")
    for child in node.get("children", []):
        v = _find_value(child, name)
        if v is not None:
            return v
    return None


class Journal:
    """Consumes /events from the patch and appends journal entries."""

    BATCH_WINDOW_S = 0.3

    def __init__(self, patch: PatchClient, store: Store, hub: Hub):
        self.patch = patch
        self.store = store
        self.hub = hub
        self.extractors = [ClassDesignExtractor(), ChangeExtractor()]
        self.last_game_time: str | None = None
        self.last_open: str | None = None

    async def run(self):
        since = None
        while True:
            try:
                data = await self.patch.events(since, wait_ms=25000)
                if since is None:
                    # Start from "now"; history before the bridge started is not replayed.
                    since = data["latest"]
                    continue
                if data["latest"] < since:
                    # Aurora restarted and its event numbering began again: replay the new process's events.
                    since = 0
                    continue
                events = data["events"]
                if not events:
                    continue
                # Fast players generate bursts; gather the rest of a burst before reading windows.
                await asyncio.sleep(self.BATCH_WINDOW_S)
                more = await self.patch.events(events[-1]["seq"], wait_ms=0)
                events += more["events"]
                since = events[-1]["seq"]
                await self.process(events)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # Aurora restarting, patch not loaded yet, ...
                # Keep our position: events recorded while we were away are fetched on reconnect.
                log.warning("event stream: %s", e)
                await asyncio.sleep(1)

    async def process(self, events: list[dict]):
        for ev in events:
            game_time = ev.get("gameTime")
            if game_time and self.last_game_time and game_time != self.last_game_time:
                self.add("game", f"game time advanced to {game_time}", game_time)
            if game_time:
                self.last_game_time = game_time

            window = window_name(ev)
            if ev["type"] == "form_focus" and (self.last_open == window or window == "AuroraPatch"):
                continue  # Focus right after opening is implied.
            self.last_open = window if ev["type"] == "form_open" else None
            if ev["type"] == "dialog_close":
                continue

            line = describe(ev)
            if line:
                where = "" if ev["type"].startswith("dialog") else f"{window}: "
                self.add(ACTORS.get(ev["source"], ev["source"]), where + line, game_time)

        for extractor in self.extractors:
            try:
                for line in await extractor.after(self.patch, events):
                    prefix = "" if extractor.WINDOW == "*" else f"{extractor.WINDOW}: "
                    self.add("game", prefix + line, self.last_game_time)
            except Exception as e:
                log.warning("extractor %s: %s", type(extractor).__name__, e)

    def add(self, actor: str, text: str, game_time: str | None):
        entry = self.store.add_journal(actor, text, game_time)
        self.hub.publish({"type": "journal", "entry": entry})


def format_entry(e: dict) -> str:
    """Journal entry as a context line: 'Sat 8 Aug 2144 05:41 · player · Class Design: ...'."""
    gt = e.get("game_time") or ""
    m = re.match(r"\w+day, (\d+) (\w+) (\d+) (\d+:\d+)", gt)
    stamp = f"{m.group(1)} {m.group(2)[:3]} {m.group(3)} {m.group(4)}" if m else gt
    return f"[{stamp}] {e['actor']}: {e['text']}"
