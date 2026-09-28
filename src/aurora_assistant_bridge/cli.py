"""Command line: `aurora-assistant` (web chat) and `aurora-assistant mcp` (MCP server)."""

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
import webbrowser

from . import __version__
from .config import Config, config_path, write_example


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aurora-assistant", description="LLM assistant for Aurora 4X.")
    parser.add_argument("--version", action="version", version=f"aurora-assistant-bridge {__version__}")
    parser.add_argument("--api-url", help="Aurora Assistant API URL (default http://127.0.0.1:47100)")
    sub = parser.add_subparsers(dest="command")

    web = sub.add_parser("web", help="run the web chat (the default)")
    mcp = sub.add_parser("mcp", help="run an MCP server exposing the game tools")
    sub.add_parser("config", help="show where the config file is, creating an example if missing")
    for p in (parser, web):
        p.add_argument("--host", help="address for the chat page (default 127.0.0.1)")
        p.add_argument("--port", type=int, help="port for the chat page (default 47110)")
        p.add_argument("--no-browser", action="store_true", help="do not open a browser")
    mcp.add_argument("--http", action="store_true", help="serve MCP over streamable HTTP instead of stdio")
    mcp.add_argument("--host", default="127.0.0.1", help="HTTP address (default 127.0.0.1)")
    mcp.add_argument("--port", type=int, default=47111, help="HTTP port (default 47111)")

    args = parser.parse_args(argv)
    path = config_path()
    if not path.exists():
        write_example(path)

    if args.command == "config":
        print(path)
        return

    if args.command == "mcp":
        # stdout carries the MCP protocol in stdio mode, so logs go to stderr.
        logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
        from .mcp_server import run_mcp
        cfg = Config.load({"api_url": args.api_url})
        run_mcp(cfg, http=args.http, host=args.host, port=args.port)
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = Config.load({"api_url": args.api_url, "host": getattr(args, "host", None), "port": getattr(args, "port", None),
                       "open_browser": False if getattr(args, "no_browser", False) else None})
    logging.info("aurora-assistant-bridge %s, config %s", __version__, path)

    if _already_running(cfg):
        # Launched again (e.g. with the game) while a bridge is running: just show its chat.
        url = f"http://localhost:{cfg.port}/"
        logging.info("already running at %s", url)
        if cfg.open_browser:
            webbrowser.open(url)
        return

    from .server import run
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass


def _already_running(cfg: Config) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.port}/api/config", timeout=2) as r:
            return json.load(r).get("app") == "aurora-assistant-bridge"
    except Exception:
        return False
