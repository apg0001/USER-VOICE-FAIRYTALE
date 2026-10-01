from collections.abc import Callable

from app.core.config import Settings
from app.models.tts.base import TTSModel
from app.models.tts.cosyvoice import COSYVOICE3_DESCRIPTOR, CosyVoice3TTSModel
from app.models.tts.mock import MockTTSModel


class TTSModelNotFoundError(KeyError):
    pass


class TTSModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], TTSModel]] = {}

    def register(self, adapter: type[TTSModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"TTS model already registered: {key}")
        self._factories[key] = adapter

    def register_factory(self, key: str, factory: Callable[[], TTSModel]) -> None:
        if key in self._factories:
            raise ValueError(f"TTS model already registered: {key}")
        self._factories[key] = factory

    def create(self, key: str) -> TTSModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise TTSModelNotFoundError(key) from error


def build_tts_model_registry(
    *,
    include_mock: bool,
    settings: Settings | None = None,
) -> TTSModelRegistry:
    registry = TTSModelRegistry()
    if include_mock:
        registry.register(MockTTSModel)
    if settings is not None and settings.enable_cosyvoice3:
        registry.register_factory(
            COSYVOICE3_DESCRIPTOR.key,
            lambda: CosyVoice3TTSModel(settings),
        )
    return registry
