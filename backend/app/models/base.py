from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ModelCapability(StrEnum):
    GENERAL_TTS = "general_tts"
    LONG_FORM_TTS = "long_form_tts"
    SPEECH_VOICE_CONVERSION = "speech_voice_conversion"
    SINGING_VOICE_CONVERSION = "singing_voice_conversion"


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    key: str
    display_name: str
    version: str
    capabilities: tuple[ModelCapability, ...]
    requires_gpu: bool
    is_mock: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class VoiceModel(ABC):
    """Stable adapter boundary for replaceable inference implementations."""

    descriptor: ModelDescriptor

    @abstractmethod
    def load(self) -> None:
        """Allocate model resources."""

    @abstractmethod
    def preprocess(self, input_data: Any) -> Any:
        """Convert validated platform input into model input."""

    @abstractmethod
    def infer(self, input_data: Any, voice_profile: dict[str, Any]) -> Any:
        """Run inference. This method is called only in an AI worker."""

    @abstractmethod
    def postprocess(self, output: Any) -> Any:
        """Convert model output into a platform audio artifact."""

    @abstractmethod
    def unload(self) -> None:
        """Release CPU/GPU resources."""

