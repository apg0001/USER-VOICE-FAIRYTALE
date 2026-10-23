from collections.abc import Callable

from app.core.config import Settings
from app.models.tts.cosyvoice import COSYVOICE3_DESCRIPTOR, CosyVoice3Model
from app.models.voice_conversion.base import VoiceConversionModel
from app.models.voice_conversion.mock import MockVoiceConversionModel


class VoiceConversionModelNotFoundError(KeyError):
    pass


class VoiceConversionModelRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], VoiceConversionModel]] = {}

    def register(self, adapter: type[VoiceConversionModel]) -> None:
        key = adapter.descriptor.key
        if key in self._factories:
            raise ValueError(f"voice conversion model already registered: {key}")
        self._factories[key] = adapter

    def register_factory(self, key: str, factory: Callable[[], VoiceConversionModel]) -> None:
        if key in self._factories:
            raise ValueError(f"voice conversion model already registered: {key}")
        self._factories[key] = factory

    def create(self, key: str) -> VoiceConversionModel:
        try:
            return self._factories[key]()
        except KeyError as error:
            raise VoiceConversionModelNotFoundError(key) from error


def build_voice_conversion_registry(
    *,
    include_mock: bool,
    settings: Settings | None = None,
) -> VoiceConversionModelRegistry:
    registry = VoiceConversionModelRegistry()
    if include_mock:
        registry.register(MockVoiceConversionModel)
    if settings is not None and settings.enable_cosyvoice3:
        registry.register_factory(
            COSYVOICE3_DESCRIPTOR.key,
            lambda: CosyVoice3Model(settings),
        )
    return registry
