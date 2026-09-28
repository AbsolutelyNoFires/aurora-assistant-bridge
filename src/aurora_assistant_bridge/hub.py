"""In-process pub/sub: pushes journal, chat and status updates to web clients and the agent."""

import asyncio


class Hub:
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subscribers.discard(q)

    def publish(self, message: dict):
        for q in list(self.subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                self.subscribers.discard(q)  # A stuck client; it will reconnect and reload history.
