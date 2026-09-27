"""Run the companion bridge: python3 -m companion_bridge"""

import asyncio
import logging

from aiohttp import web

from .agent import Agent
from .config import Config
from .hub import Hub
from .journal import Journal
from .patch_client import PatchClient
from .store import Store
from .web import make_app


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    cfg = Config()
    store = Store(cfg.data_dir / "bridge.db")
    hub = Hub()
    patch = PatchClient(cfg.patch_url)
    await patch.start()

    journal = Journal(patch, store, hub)
    agent = Agent(cfg, store, hub, patch)
    app = make_app(store, hub, agent, cfg.vnc_ws_url)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, cfg.host, cfg.port).start()
    logging.info("bridge on http://%s:%d (LLM: %s)", cfg.host, cfg.port, cfg.llm_base_url or "not configured")

    await asyncio.gather(journal.run(), agent.run())


if __name__ == "__main__":
    asyncio.run(main())
