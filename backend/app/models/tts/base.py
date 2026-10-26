from dataclasses import dataclass
from typing import Any, Protocol

from app.models.base import ModelDescriptor


@dataclass(frozen=True, slots=True)
class TTSAudio:
    pcm_s16le: bytes
    sample_rate: int


class TTSModel(Protocol):
    descriptor: ModelDescriptor

    def load(self) -> None: ...

    def synthesize(self, text: str, voice_profile: dict[str, Any]) -> TTSAudio: ...

    def unload(self) -> None: ...
