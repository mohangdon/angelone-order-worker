import json
import os
import threading
from pathlib import Path


class JsonStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def load(self, default):
        with self.lock:
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return default

    def save(self, value):
        with self.lock:
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
            os.replace(temp, self.path)
