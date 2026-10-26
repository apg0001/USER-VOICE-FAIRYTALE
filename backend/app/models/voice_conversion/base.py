from typing import Any, Protocol

from app.models.base import ModelDescriptor


class VoiceConversionModel(Protocol):
    descriptor: ModelDescriptor

    def load(self) -> None: ...

    def convert(
        self, pcm_s16le: bytes, sample_rate: int, voice_profile: dict[str, Any]
    ) -> bytes: ...

    def unload(self) -> None: ...
