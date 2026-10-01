import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.storage.base import ObjectStorage, StoredObject


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

    async def healthcheck(self) -> None:
        def probe() -> None:
            descriptor, name = tempfile.mkstemp(prefix=".health-", dir=self.root)
            os.close(descriptor)
            Path(name).unlink(missing_ok=True)

        await asyncio.to_thread(probe)

    async def list_objects(self, prefix: str) -> list[StoredObject]:
        root = self._safe_path(prefix.strip("/\\"))

        def scan() -> list[StoredObject]:
            if not root.exists():
                return []
            return [
                StoredObject(
                    key=path.relative_to(self.root).as_posix(),
                    modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                )
                for path in root.rglob("*")
                if path.is_file()
            ]

        return await asyncio.to_thread(scan)
