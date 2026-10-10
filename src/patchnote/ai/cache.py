"""Content-addressed cache for LLM responses."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def key(self, mode: str, model: str, base_url: str, payload: Any) -> str:
        blob = json.dumps(
            {"mode": mode, "model": model, "base_url": base_url, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self.path_for(key)
        try:
            if not path.is_file():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
