"""SQLite persistence for the journal and the chat transcript."""

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY,
    time REAL NOT NULL,
    game_time TEXT,
    actor TEXT NOT NULL,          -- player | companion | game
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY,
    time REAL NOT NULL,
    role TEXT NOT NULL,           -- user | assistant | tool
    content TEXT,
    tool_calls TEXT,              -- JSON (assistant)
    tool_call_id TEXT,            -- (tool)
    name TEXT                     -- tool name (tool)
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # Journal ------------------------------------------------------------------------------

    def add_journal(self, actor: str, text: str, game_time: str | None) -> dict:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO journal (time, game_time, actor, text) VALUES (?, ?, ?, ?)", (now, game_time, actor, text)
        )
        self.db.commit()
        return {"id": cur.lastrowid, "time": now, "game_time": game_time, "actor": actor, "text": text}

    def journal_since(self, after_id: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM journal WHERE id > ? ORDER BY id", (after_id,))
        return [dict(r) for r in rows]

    def journal_tail(self, limit: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM (SELECT * FROM journal ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
        return [dict(r) for r in rows]

    # Chat ---------------------------------------------------------------------------------

    def add_chat(self, role: str, content: str | None, tool_calls=None, tool_call_id=None, name=None) -> dict:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO chat (time, role, content, tool_calls, tool_call_id, name) VALUES (?, ?, ?, ?, ?, ?)",
            (now, role, content, json.dumps(tool_calls) if tool_calls else None, tool_call_id, name),
        )
        self.db.commit()
        return self._chat_row(self.db.execute("SELECT * FROM chat WHERE id = ?", (cur.lastrowid,)).fetchone())

    def chat_since(self, after_id: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM chat WHERE id > ? ORDER BY id", (after_id,))
        return [self._chat_row(r) for r in rows]

    def chat_tail(self, limit: int) -> list[dict]:
        rows = self.db.execute("SELECT * FROM (SELECT * FROM chat ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))
        return [self._chat_row(r) for r in rows]

    @staticmethod
    def _chat_row(r) -> dict:
        d = dict(r)
        d["tool_calls"] = json.loads(d["tool_calls"]) if d["tool_calls"] else None
        return d
