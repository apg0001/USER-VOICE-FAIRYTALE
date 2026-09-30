from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path


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

