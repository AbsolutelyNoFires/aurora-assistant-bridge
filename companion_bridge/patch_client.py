"""Async client for the in-game Companion patch HTTP API."""

from urllib.parse import quote

import aiohttp


class PatchError(Exception):
    pass


class PatchClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90))

    async def close(self):
        if self.session:
            await self.session.close()

    async def _get(self, path: str, **params):
        async with self.session.get(self.base_url + path, params=params) as r:
            if r.content_type == "application/json":
                data = await r.json()
            else:
                data = await r.text()
            if r.status >= 400:
                raise PatchError(data.get("error") if isinstance(data, dict) else str(data))
            return data

    async def _post(self, path: str, body: dict):
        async with self.session.post(self.base_url + path, json=body) as r:
            data = await r.json()
            if r.status >= 400 and not (isinstance(data, dict) and "status" in data):
                raise PatchError(data.get("error", str(data)))
            return data

    async def health(self):
        return await self._get("/health")

    async def forms(self):
        return await self._get("/forms")

    async def form_text(self, form: str, **params) -> str:
        return await self._get("/forms/" + quote(form, safe=""), **params)

    async def form_json(self, form: str, **params) -> dict:
        return await self._get("/forms/" + quote(form, safe=""), format="json", **params)

    async def action(self, form: str, control: str, action: str, **args) -> dict:
        path = f"/forms/{quote(form, safe='')}/controls/{quote(control, safe='')}/{action}"
        return await self._post(path, {k: str(v) for k, v in args.items() if v is not None})

    async def events(self, since: int | None, wait_ms: int = 25000) -> dict:
        params = {"wait": wait_ms}
        if since is not None:
            params["since"] = since
        return await self._get("/events", **params)

    async def dialogs(self) -> list:
        return await self._get("/dialogs")

    async def answer_dialog(self, dialog_id: str, button: str) -> dict:
        return await self._post(f"/dialogs/{quote(dialog_id, safe='')}/click", {"button": button})
