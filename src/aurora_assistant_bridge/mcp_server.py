"""MCP server exposing the game tools, for MCP clients (Claude Desktop, Claude Code, ...).

Uses the same tools as the built-in assistant. `recent_activity` reads the journal kept by a running
web bridge, or falls back to the game's raw UI event stream.
"""

import sqlite3
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Config
from .journal import describe, format_entry, window_name
from .patch_client import PatchClient
from .tools import TOOLS, run_tool

INSTRUCTIONS = """Tools for Aurora 4X (C# edition), a deep 4X space strategy game, running on the user's computer \
through the Aurora Assistant API patch. Read any open window as text, and operate windows like a player would.

- Windows are named as list_windows shows them (e.g. "Class Design", "Economics", "Tactical Map"). Main windows \
open from toolbar buttons on the Tactical Map (cmdToolbarClass = Class Design, cmdToolbarResearch = research, ...).
- read_window returns an outline: Kind name "caption": "text" = "value". Use the exact control names it shows.
- Action tools return their effects: windows opened/closed, message boxes, values that changed. If an action \
opens an input window, fill it with set_value and click its OK button; answer message boxes with answer_dialog.
- recent_activity shows what the player (and assistants) have been doing, with in-game dates.
- The player may be using the game at the same time; ask before destructive or irreversible actions."""


def _describe(name: str) -> str:
    return next(t["function"]["description"] for t in TOOLS if t["function"]["name"] == name)


def build_server(cfg: Config) -> MCPServer:
    client: dict[str, PatchClient] = {}

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield None
        finally:
            if "p" in client:
                await client.pop("p").close()

    server = MCPServer("aurora-assistant", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)

    async def patch() -> PatchClient:
        if "p" not in client:
            client["p"] = PatchClient(cfg.api_url)
            await client["p"].start()
        return client["p"]

    async def call(name: str, **args) -> str:
        return await run_tool(await patch(), name, {k: v for k, v in args.items() if v is not None})

    @server.tool(description="Game date, race and open windows.")
    async def game_status() -> str:
        p = await patch()
        health = await p.health()
        title = (health.get("title") or "").split("   ")
        windows = await call("list_windows")
        return (f"Race: {title[0].strip() if title else '?'}\nGame date: {title[1].strip() if len(title) > 1 else '?'}\n"
                f"API version: {health.get('version')}\nOpen windows: {windows}")

    @server.tool(description=_describe("list_windows"))
    async def list_windows() -> str:
        return await call("list_windows")

    @server.tool(description=_describe("read_window"))
    async def read_window(window: str, all_tree_nodes: bool = False, combo_options: bool = False) -> str:
        return await call("read_window", window=window, all_tree_nodes=all_tree_nodes, combo_options=combo_options)

    @server.tool(description=_describe("click"))
    async def click(window: str, control: str) -> str:
        return await call("click", window=window, control=control)

    @server.tool(description=_describe("set_value"))
    async def set_value(window: str, control: str, value: str) -> str:
        return await call("set_value", window=window, control=control, value=value)

    @server.tool(description=_describe("select"))
    async def select(window: str, control: str, value: str) -> str:
        return await call("select", window=window, control=control, value=value)

    @server.tool(description=_describe("double_click"))
    async def double_click(window: str, control: str, value: str | None = None) -> str:
        return await call("double_click", window=window, control=control, value=value)

    @server.tool(description=_describe("answer_dialog"))
    async def answer_dialog(button: str) -> str:
        return await call("answer_dialog", button=button)

    @server.tool(description="Recent game activity (what the player and assistants did, with in-game dates), oldest first.")
    async def recent_activity(limit: int = 50) -> str:
        db = cfg.data_path / "bridge.db"
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            floor = con.execute("SELECT value FROM meta WHERE key = 'journal_floor'").fetchone()
            rows = con.execute("SELECT * FROM (SELECT * FROM journal WHERE id > ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                               (int(floor[0]) if floor else 0, limit)).fetchall()
            con.close()
            if rows:
                return "\n".join(format_entry(dict(r)) for r in rows)
        # No web bridge journal: summarise the game's raw UI events instead.
        data = await (await patch()).events(0, wait_ms=0)
        lines = []
        for ev in data["events"][-limit:]:
            line = describe(ev)
            if line:
                who = {"user": "player", "api": "assistant"}.get(ev["source"], ev["source"])
                lines.append(f"[{ev.get('gameTime') or ''}] {who}: {window_name(ev)}: {line}")
        return "\n".join(lines) or "No activity recorded yet."

    return server


def run_mcp(cfg: Config, http: bool = False, host: str = "127.0.0.1", port: int = 47111):
    import anyio

    server = build_server(cfg)
    if http:
        kwargs = {"host": host, "port": port}
        if host not in ("127.0.0.1", "localhost", "::1"):
            # Serving on the network: accept requests addressed to this machine's LAN/Tailscale names.
            from mcp.server.transport_security import TransportSecuritySettings
            kwargs["transport_security"] = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        anyio.run(lambda: server.run_streamable_http_async(**kwargs))
    else:
        anyio.run(server.run_stdio_async)
