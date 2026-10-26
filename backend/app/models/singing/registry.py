from app.models.singing.base import SingingVoiceModel
from app.models.singing.mock import MockSingingVoiceModel


class SingingModelNotFoundError(KeyError):
    pass


class SingingModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, type[SingingVoiceModel]] = {}

    def register(self, adapter: type[SingingVoiceModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"singing model already registered: {key}")
        self._factories[key] = adapter

    def create(self, key: str) -> SingingVoiceModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise SingingModelNotFoundError(key) from error


def build_singing_registry(*, include_mock: bool) -> SingingModelRegistry:
    registry = SingingModelRegistry()
    if include_mock:
        registry.register(MockSingingVoiceModel)
    return registry
