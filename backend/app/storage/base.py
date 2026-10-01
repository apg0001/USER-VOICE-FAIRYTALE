from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    modified_at: datetime


class ObjectStorage(ABC):
    """Storage boundary that can be implemented by local disk, S3 or MinIO."""

    @abstractmethod
    async def put(self, source: Path, *, namespace: str, suffix: str) -> str:
        pass

    @abstractmethod
    def open(self, key: str) -> AsyncIterator[bytes]:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass

    @abstractmethod
    async def healthcheck(self) -> None:
        """Raise when this process cannot safely use the storage backend."""

        pass

    @abstractmethod
    async def list_objects(self, prefix: str) -> list[StoredObject]:
        """List objects below a controlled namespace for reconciliation."""

        pass
