"""Runs the bridge: game journal, assistant loop and the web chat."""

import asyncio
import logging
import webbrowser

from aiohttp import web

from .agent import Agent
from .config import Config
from .hub import Hub
from .journal import Journal
from .patch_client import PatchClient
from .store import Store
from .web import make_app

log = logging.getLogger("bridge")


async def run(cfg: Config):
    store = Store(cfg.data_path / "bridge.db")
    hub = Hub()
    patch = PatchClient(cfg.api_url)
    await patch.start()

    journal = Journal(patch, store, hub)
    agent = Agent(cfg, store, hub, patch)
    app = make_app(store, hub, agent, cfg)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, cfg.host, cfg.port).start()
    url = f"http://{'localhost' if cfg.host in ('127.0.0.1', '0.0.0.0', '::') else cfg.host}:{cfg.port}/"
    log.info("chat on %s  (game API %s, LLM %s)", url, cfg.api_url,
             f"{cfg.llm_model} at {cfg.llm_base_url}" if cfg.llm_enabled else "not configured")
    if cfg.open_browser:
        webbrowser.open(url)

    await asyncio.gather(journal.run(), agent.run())
