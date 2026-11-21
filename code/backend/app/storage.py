"""File storage behind a tiny interface (local disk now, Supabase/S3 later).

Keys are content-addressed where possible, so the same scan uploaded twice is
stored once.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from .config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if path != self.root and not path.is_relative_to(self.root):
            raise ValueError("invalid storage key")
        return path

    async def put(self, key: str, data: bytes) -> str:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic: readers never see half a file

        await asyncio.to_thread(_write)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))

    def exists(self, key: str) -> bool:
        try:
            return self._path(key).is_file()
        except ValueError:
            return False

    def local_path(self, key: str) -> Path:
        return self._path(key)


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(get_settings().storage_dir)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
