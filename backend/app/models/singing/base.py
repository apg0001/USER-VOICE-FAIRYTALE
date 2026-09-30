from typing import Any, Protocol

from app.models.base import ModelDescriptor


class SingingVoiceModel(Protocol):
    descriptor: ModelDescriptor

    def load(self) -> None: ...

    def convert(
        self,
        vocal_pcm_s16le: bytes,
        sample_rate: int,
        channels: int,
        voice_profile: dict[str, Any],
    ) -> bytes: ...

    def unload(self) -> None: ...
