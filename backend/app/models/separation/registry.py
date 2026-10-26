from collections.abc import Callable

from app.core.config import Settings
from app.models.separation.base import SeparationModel
from app.models.separation.demucs import HTDEMUCS_DESCRIPTOR, HTDemucsSeparationModel
from app.models.separation.mock import MockSeparationModel


class SeparationModelNotFoundError(KeyError):
    pass


class SeparationModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], SeparationModel]] = {}

    def register(self, adapter: type[SeparationModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"separation model already registered: {key}")
        self._factories[key] = adapter

    def register_factory(self, key: str, factory: Callable[[], SeparationModel]) -> None:
        if key in self._factories:
            raise ValueError(f"separation model already registered: {key}")
        self._factories[key] = factory

    def create(self, key: str) -> SeparationModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise SeparationModelNotFoundError(key) from error


def build_separation_registry(
    *,
    include_mock: bool,
    settings: Settings | None = None,
) -> SeparationModelRegistry:
    registry = SeparationModelRegistry()
    if include_mock:
        registry.register(MockSeparationModel)
    if settings is not None and settings.enable_demucs:
        registry.register_factory(
            HTDEMUCS_DESCRIPTOR.key,
            lambda: HTDemucsSeparationModel(settings),
        )
    return registry


def separator_key(settings: Settings) -> str:
    """The separator is a fixed pipeline stage, not a user-selected model."""
    return HTDEMUCS_DESCRIPTOR.key if settings.enable_demucs else "mock-separator-v1"
