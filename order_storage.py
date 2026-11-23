"""Persist Telegram order conversations on the service volume."""
import json
import os
import sqlite3
from pathlib import Path

from aiogram.fsm.storage.memory import MemoryStorage

import config


def db_path(name):
    root = Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or config.DATA_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root / name


class OrderStorage(MemoryStorage):
    def __init__(self, path=None):
        super().__init__()
        self.db = sqlite3.connect(path or db_path("order-drafts.sqlite3"))
        self.db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, state TEXT, data TEXT)")

    def _id(self, key):
        return json.dumps(vars(key), sort_keys=True)

    def _row(self, key):
        return self.db.execute("SELECT state,data FROM drafts WHERE id=?", (self._id(key),)).fetchone()

    async def get_state(self, key):
        row = self._row(key)
        return row[0] if row else await super().get_state(key)

    async def get_data(self, key):
        row = self._row(key)
        return json.loads(row[1]) if row else await super().get_data(key)

    async def set_state(self, key, state=None):
        value = getattr(state, "state", state)
        if value and value.startswith(("Order:", "AIOrder:")):
            data = await self.get_data(key)
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,?)",
                                (self._id(key), value, json.dumps(data, ensure_ascii=False)))
            self.storage.pop(key, None)
        else:
            with self.db:
                self.db.execute("DELETE FROM drafts WHERE id=?", (self._id(key),))
            await super().set_state(key, state)

    async def set_data(self, key, data):
        with self.db:
            result = self.db.execute("UPDATE drafts SET data=? WHERE id=?",
                                     (json.dumps(dict(data), ensure_ascii=False), self._id(key)))
        if not result.rowcount:
            await super().set_data(key, data)

    async def get_value(self, key, dict_key, default=None):
        return (await self.get_data(key)).get(dict_key, default)

    async def close(self):
        self.db.close()
