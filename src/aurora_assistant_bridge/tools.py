"""Tools the assistant can call, in OpenAI function format, executed against the patch API."""

import asyncio
import json
import re

from .journal import _flatten, humanize
from .patch_client import PatchClient, PatchError

MAX_RESULT_CHARS = 12000


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_window = {"type": "string", "description": "Window name as shown by list_windows, e.g. \"Class Design\" or \"Tactical Map\"."}
_control = {"type": "string", "description": "Control name from read_window, e.g. \"cmdDesignTech\"."}

TOOLS = [
    _fn("list_windows", "List Aurora's open windows.", {}, []),
    _fn("read_window", "Read a window's controls and their text/values as an indented outline. "
        "Controls are shown as: Kind name \"caption\": \"text\" = \"value\".",
        {"window": _window,
         "all_tree_nodes": {"type": "boolean", "description": "Expand collapsed tree nodes too."},
         "combo_options": {"type": "boolean", "description": "Include every drop-down option."}},
        ["window"]),
    _fn("click", "Click a button. Action tools return their effects: windows opened/closed, message boxes, "
        "and values that changed. If a click opens an input window (e.g. \"Enter New Class Name\"), fill its "
        "text box with set_value and click its OK button. To open a main window, click its toolbar button on the Tactical Map "
        "(cmdToolbarClass = Class Design, cmdToolbarResearch = Research, cmdToolbarFleet = Naval Organisation, "
        "cmdToolbarColony = Economics, cmdToolbarTechnology = Technology, cmdToolbarEvents = Events).",
        {"window": _window, "control": _control}, ["window", "control"]),
    _fn("set_value", "Type into a text box or set a checkbox (value true/false).",
        {"window": _window, "control": _control, "value": {"type": "string"}}, ["window", "control", "value"]),
    _fn("select", "Choose an item in a drop-down, list, tree or tab strip. Tree items use a path like \"Freighter > Freighter (8)\".",
        {"window": _window, "control": _control, "value": {"type": "string"}}, ["window", "control", "value"]),
    _fn("double_click", "Double-click an item in a list or tree (selects it first when value is given). "
        "In Class Design: double-clicking a component in tvComponents adds it to the class; to remove one, "
        "first set rdoClass (\"Class Components\") to true, which shows the class's own components in tvInClass, "
        "and double-click it there (set rdoRace to true to go back). The 1/5/20/100 radio buttons "
        "(rdo1, rdo5, rdo20, rdo100) set how many are added or removed per double-click.",
        {"window": _window, "control": _control,
         "value": {"type": "string", "description": "Item to double-click, e.g. a tree path \"Propulsion > Engines > …\"."}},
        ["window", "control"]),
    _fn("answer_dialog", "Press a button on the open message box, e.g. \"OK\", \"Yes\", \"No\".",
        {"button": {"type": "string"}}, ["button"]),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def text_mode_instructions() -> str:
    """Tool description for models without native tool calling (TOOL_MODE=text)."""
    lines = ["To act, reply with one or more fenced JSON blocks like:",
             '```json\n{"tool": "read_window", "args": {"window": "Class Design"}}\n```',
             "Results come back in the next message. Available tools:"]
    for t in TOOLS:
        f = t["function"]
        args = ", ".join(f["parameters"]["properties"])
        lines.append(f"- {f['name']}({args}): {f['description']}")
    return "\n".join(lines)


async def run_tool(patch: PatchClient, name: str, args: dict) -> str:
    try:
        result = await _run(patch, name, args)
    except PatchError as e:
        result = f"error: {e}"
    except Exception as e:
        result = f"error: {type(e).__name__}: {e}"
    if not isinstance(result, str):
        result = json.dumps(result, ensure_ascii=False)
    if len(result) > MAX_RESULT_CHARS:
        result = result[:MAX_RESULT_CHARS] + f"\n… truncated ({len(result)} chars)"
    return result


ACTIONS = {"click", "set_value", "select", "double_click", "answer_dialog"}
MAX_CHANGES = 12
MAX_LINE_DIFF = 10


async def _snapshot(patch: PatchClient, window: str | None) -> tuple[dict, dict]:
    """(open windows by name, flattened control values) for computing effects. Covers the acted-on
    window and every other open window except the map, since e.g. OK in a popup changes the window
    behind it. Keys are "window|control|label"."""
    try:
        forms = {f["name"]: f for f in await patch.forms() if f["type"] != "AuroraPatchForm"}
    except Exception:
        forms = {}
    state: dict = {}
    for name in forms:
        if name == "Tactical Map" and window != "Tactical Map":
            continue
        try:
            flat: dict = {}
            _flatten(await patch.form_json(name), flat, raw=True)
            state.update({f"{name}|{k}": v for k, v in flat.items()})
        except Exception:
            pass
    return forms, state


def _effects(before: tuple[dict, dict], after: tuple[dict, dict], window: str | None, touched: str | None) -> list[str]:
    forms_b, state_b = before
    forms_a, state_a = after
    out = []
    for name in forms_a.keys() - forms_b.keys():
        out.append(f'window opened: "{name}"' + (" (active)" if forms_a[name]["active"] else ""))
    for name in forms_b.keys() - forms_a.keys():
        out.append(f'window closed: "{name}"')
    changes = []
    for key, new in state_a.items():
        old = state_b.get(key)
        win, ctl, label = key.split("|", 2)
        if old is None or old == new or (ctl == touched and win == window):
            continue
        label = (label or humanize(ctl)) + ("" if win == window else f" in {win}")
        if "\n" in old or "\n" in new:
            ol = [l.strip() for l in old.splitlines() if l.strip()]
            nl = [l.strip() for l in new.splitlines() if l.strip()]
            removed = [l for l in ol if l not in nl]
            added = [l for l in nl if l not in ol]
            if len(removed) == len(ol):
                removed = []  # Entirely replaced (e.g. a detail box showing another item): just show the new text.
            diff = [f"  - {l}" for l in removed][:MAX_LINE_DIFF] + [f"  + {l}" for l in added][:MAX_LINE_DIFF]
            if diff:
                changes.append(f"{label} ({ctl}) changed:\n" + "\n".join(re.sub(r"\s{2,}", "  ", d) for d in diff))
        else:
            changes.append(f'{label} ({ctl}): "{old[:120]}" → "{new[:120]}"')
    out += changes[:MAX_CHANGES]
    if len(changes) > MAX_CHANGES:
        out.append(f"(+{len(changes) - MAX_CHANGES} more changes; read_window to see everything)")
    return out


async def _run(patch: PatchClient, name: str, a: dict):
    if name in ACTIONS:
        window = a.get("window")
        before = await _snapshot(patch, window)
        result = await _act(patch, name, a)
        if isinstance(result, dict) and result.get("status") == "error":
            return result
        await asyncio.sleep(0.3)
        after = await _snapshot(patch, window)
        effects = _effects(before, after, window, a.get("control"))
        dialogs = await patch.dialogs()
        text = [f"status: {result.get('status', 'done') if isinstance(result, dict) else result}"]
        if isinstance(result, dict) and result.get("status") == "running":
            text.append("the action is waiting on a window or message box it opened")
        for d in dialogs:
            text.append(f'message box open: "{d["message"]}" buttons: {", ".join(d["buttons"])} (use answer_dialog)')
        text += effects or ["no visible changes"]
        return "\n".join(text)
    return await _act(patch, name, a)


async def _act(patch: PatchClient, name: str, a: dict):
    if name == "list_windows":
        forms = await patch.forms()
        return [{"window": f["name"], "active": f["active"]} for f in forms if f["type"] != "AuroraPatchForm"]
    if name == "read_window":
        params = {}
        if a.get("all_tree_nodes"):
            params["alltree"] = 1
        if a.get("combo_options"):
            params["items"] = 1
        return await patch.form_text(a["window"], **params)
    if name == "click":
        return await patch.action(a["window"], a["control"], "click")
    if name == "set_value":
        return await patch.action(a["window"], a["control"], "set", value=a["value"])
    if name == "select":
        return await patch.action(a["window"], a["control"], "select", value=a["value"])
    if name == "double_click":
        return await patch.action(a["window"], a["control"], "doubleclick", value=a.get("value"))
    if name == "answer_dialog":
        dialogs = await patch.dialogs()
        if not dialogs:
            return "error: no message box is open"
        return await patch.answer_dialog(dialogs[-1]["id"], a["button"])
    return f"error: unknown tool {name}"
