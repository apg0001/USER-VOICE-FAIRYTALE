from app.models.separation.base import SeparationModel
from app.models.separation.mock import MockSeparationModel


class SeparationModelNotFoundError(KeyError):
    pass


class SeparationModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, type[SeparationModel]] = {}

    def register(self, adapter: type[SeparationModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"separation model already registered: {key}")
        self._factories[key] = adapter

    def create(self, key: str) -> SeparationModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise SeparationModelNotFoundError(key) from error


def build_separation_registry(*, include_mock: bool) -> SeparationModelRegistry:
    registry = SeparationModelRegistry()
    if include_mock:
        registry.register(MockSeparationModel)
    return registry
