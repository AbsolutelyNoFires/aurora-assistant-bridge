"""Tools the companion can call, in OpenAI function format, executed against the patch API."""

import json

from .patch_client import PatchClient, PatchError

MAX_RESULT_CHARS = 12000


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


_window = {"type": "string", "description": "Window title as shown by list_windows, e.g. \"Class Design\", or \"TacticalMapForm\" for the main map."}
_control = {"type": "string", "description": "Control name from read_window, e.g. \"cmdDesignTech\"."}

TOOLS = [
    _fn("list_windows", "List Aurora's open windows.", {}, []),
    _fn("read_window", "Read a window's controls and their text/values as an indented outline. "
        "Controls are shown as: Kind name \"caption\": \"text\" = \"value\".",
        {"window": _window,
         "all_tree_nodes": {"type": "boolean", "description": "Expand collapsed tree nodes too."},
         "combo_options": {"type": "boolean", "description": "Include every drop-down option."}},
        ["window"]),
    _fn("click", "Click a button. To open a main window, click its toolbar button on TacticalMapForm "
        "(cmdToolbarClass = Class Design, cmdToolbarResearch = Research, cmdToolbarFleet = Naval Organisation, "
        "cmdToolbarColony = Economics, cmdToolbarTechnology = Technology, cmdToolbarEvents = Events).",
        {"window": _window, "control": _control}, ["window", "control"]),
    _fn("set_value", "Type into a text box or set a checkbox (value true/false).",
        {"window": _window, "control": _control, "value": {"type": "string"}}, ["window", "control", "value"]),
    _fn("select", "Choose an item in a drop-down, list, tree or tab strip. Tree items use a path like \"Freighter > Freighter (8)\".",
        {"window": _window, "control": _control, "value": {"type": "string"}}, ["window", "control", "value"]),
    _fn("double_click", "Double-click a list or tree control (acts on its selected item).",
        {"window": _window, "control": _control}, ["window", "control"]),
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


async def _run(patch: PatchClient, name: str, a: dict):
    if name == "list_windows":
        forms = await patch.forms()
        return [{"window": f.get("known") if f.get("known") == "TacticalMapForm" else f["title"], "active": f["active"]}
                for f in forms if f["type"] != "AuroraPatchForm"]
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
        return await patch.action(a["window"], a["control"], "doubleclick")
    if name == "answer_dialog":
        dialogs = await patch.dialogs()
        if not dialogs:
            return "error: no message box is open"
        return await patch.answer_dialog(dialogs[-1]["id"], a["button"])
    return f"error: unknown tool {name}"
