from collections.abc import Iterable

from app.models.base import ModelCapability, ModelDescriptor, VoiceModel


class ModelNotFoundError(KeyError):
    pass


class ModelRegistry:
    """Maps stable model keys and capabilities to adapter factories."""

    def __init__(self) -> None:
        self._factories: dict[str, type[VoiceModel]] = {}
        self._descriptors: dict[str, ModelDescriptor] = {}

    def register(self, adapter: type[VoiceModel]) -> None:
        key = adapter.descriptor.key
        if key in self._descriptors:
            raise ValueError(f"model already registered: {key}")
        self._factories[key] = adapter
        self._descriptors[key] = adapter.descriptor

    def register_descriptor(self, descriptor: ModelDescriptor) -> None:
        if descriptor.key in self._descriptors:
            raise ValueError(f"model already registered: {descriptor.key}")
        self._descriptors[descriptor.key] = descriptor

    def create(self, key: str) -> VoiceModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise ModelNotFoundError(key) from error

    def descriptors(self, capability: ModelCapability | None = None) -> Iterable[ModelDescriptor]:
        descriptors = iter(self._descriptors.values())
        if capability is None:
            return tuple(descriptors)
        return tuple(item for item in descriptors if capability in item.capabilities)
