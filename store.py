import asyncio
import json
import os
import threading
from pathlib import Path


class JsonStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.async_lock = asyncio.Lock()

    def load(self, default):
        with self.lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return default

    def save(self, value):
        self._write(json.dumps(value, separators=(",", ":"), default=str))

    def _write(self, payload):
        with self.lock:
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            with temp.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)

    async def save_async(self, value):
        async with self.async_lock:
            payload = json.dumps(value, separators=(",", ":"), default=str)
            task = asyncio.create_task(asyncio.to_thread(self._write, payload))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
