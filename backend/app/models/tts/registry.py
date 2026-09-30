from app.models.tts.base import TTSModel
from app.models.tts.mock import MockTTSModel


class TTSModelNotFoundError(KeyError):
    pass


class TTSModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, type[TTSModel]] = {}

    def register(self, adapter: type[TTSModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"TTS model already registered: {key}")
        self._factories[key] = adapter

    def create(self, key: str) -> TTSModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise TTSModelNotFoundError(key) from error


def build_tts_model_registry(*, include_mock: bool) -> TTSModelRegistry:
    registry = TTSModelRegistry()
    if include_mock:
        registry.register(MockTTSModel)
    return registry
