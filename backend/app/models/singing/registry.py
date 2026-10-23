from collections.abc import Callable

from app.core.config import Settings
from app.models.singing.base import SingingVoiceModel
from app.models.singing.mock import MockSingingVoiceModel
from app.models.singing.seed_vc import SEED_VC_DESCRIPTOR, SeedVCSingingModel


class SingingModelNotFoundError(KeyError):
    pass


class SingingModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], SingingVoiceModel]] = {}

    def register(self, adapter: type[SingingVoiceModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"singing model already registered: {key}")
        self._factories[key] = adapter

    def register_factory(self, key: str, factory: Callable[[], SingingVoiceModel]) -> None:
        if key in self._factories:
            raise ValueError(f"singing model already registered: {key}")
        self._factories[key] = factory

    def create(self, key: str) -> SingingVoiceModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise SingingModelNotFoundError(key) from error


def build_singing_registry(
    *,
    include_mock: bool,
    settings: Settings | None = None,
) -> SingingModelRegistry:
    registry = SingingModelRegistry()
    if include_mock:
        registry.register(MockSingingVoiceModel)
    if settings is not None and settings.seed_vc_available:
        registry.register_factory(SEED_VC_DESCRIPTOR.key, lambda: SeedVCSingingModel(settings))
    return registry
