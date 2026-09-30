from app.models.voice_conversion.base import VoiceConversionModel
from app.models.voice_conversion.mock import MockVoiceConversionModel


class VoiceConversionModelNotFoundError(KeyError):
    pass


class VoiceConversionModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, type[VoiceConversionModel]] = {}

    def register(self, adapter: type[VoiceConversionModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"voice conversion model already registered: {key}")
        self._factories[key] = adapter

    def create(self, key: str) -> VoiceConversionModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise VoiceConversionModelNotFoundError(key) from error


def build_voice_conversion_registry(
    *, include_mock: bool
) -> VoiceConversionModelRegistry:
    registry = VoiceConversionModelRegistry()
    if include_mock:
        registry.register(MockVoiceConversionModel)
    return registry
