import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

from app.storage.base import ObjectStorage


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("invalid storage key")
        return candidate

    async def put(self, source: Path, *, namespace: str, suffix: str) -> str:
        safe_namespace = namespace.strip("/\\").replace("..", "")
        safe_suffix = suffix.lower().lstrip(".")
        key = f"{safe_namespace}/{uuid4()}.{safe_suffix}"
        destination = self._safe_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = await asyncio.to_thread(source.read_bytes)
        await asyncio.to_thread(destination.write_bytes, content)
        return key

    async def open(self, key: str) -> AsyncIterator[bytes]:
        path = self._safe_path(key)
        with path.open("rb") as stream:
            while chunk := await asyncio.to_thread(stream.read, 1024 * 1024):
                yield chunk

    async def delete(self, key: str) -> None:
        path = self._safe_path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

