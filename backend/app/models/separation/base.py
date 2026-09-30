from dataclasses import dataclass
from typing import Protocol

from app.models.base import ModelDescriptor


@dataclass(frozen=True, slots=True)
class SeparatedAudio:
    vocal_pcm_s16le: bytes
    instrumental_pcm_s16le: bytes


class SeparationModel(Protocol):
    descriptor: ModelDescriptor

    def load(self) -> None: ...

    def separate(self, pcm_s16le: bytes, sample_rate: int, channels: int) -> SeparatedAudio: ...

    def unload(self) -> None: ...
