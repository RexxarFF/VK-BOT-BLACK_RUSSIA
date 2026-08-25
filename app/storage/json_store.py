from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

class JsonStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    def _path(self, name: str) -> Path:
        return self.root / f'{name}.json'

    async def read(self, name: str, default: Any) -> Any:
        async with self._lock(name):
            path = self._path(name)
            if not path.exists():
                return deepcopy(default)
            try:
                return json.loads(path.read_text('utf-8'))
            except (json.JSONDecodeError, OSError):
                return deepcopy(default)

    async def write(self, name: str, data: Any) -> None:
        async with self._lock(name):
            await self._write_unlocked(name, data)

    async def update(self, name: str, default: Any, fn: Callable[[Any], Any]) -> Any:
        async with self._lock(name):
            path = self._path(name)
            if path.exists():
                try:
                    data = json.loads(path.read_text('utf-8'))
                except (json.JSONDecodeError, OSError):
                    data = deepcopy(default)
            else:
                data = deepcopy(default)
            result = fn(data)
            if result is not None:
                data = result
            await self._write_unlocked(name, data)
            return deepcopy(data)

    async def _write_unlocked(self, name: str, data: Any) -> None:
        path = self._path(name)
        tmp = path.with_suffix('.json.tmp')
        backup = path.with_suffix('.json.bak')
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, 'utf-8')
        if path.exists():
            try:
                backup.write_bytes(path.read_bytes())
            except OSError:
                pass
        os.replace(tmp, path)
