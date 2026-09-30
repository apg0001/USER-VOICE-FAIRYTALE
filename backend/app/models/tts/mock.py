import math
from array import array
from typing import Any

from app.models.base import ModelCapability, ModelDescriptor
from app.models.tts.base import TTSAudio


class MockTTSModel:
    descriptor = ModelDescriptor(
        key="mock-universal-v1",
        display_name="Mock Universal Voice Model",
        version="1.0.0",
        capabilities=(ModelCapability.GENERAL_TTS, ModelCapability.LONG_FORM_TTS),
        requires_gpu=False,
        is_mock=True,
        metadata={"output": "mono-pcm-s16le", "sample_rate": 24_000},
    )

    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def synthesize(self, text: str, voice_profile: dict[str, Any]) -> TTSAudio:
        if not self.loaded:
            raise RuntimeError("model must be loaded before inference")
        sample_rate = 24_000
        duration = min(3.0, max(0.25, len(text.strip()) * 0.035))
        fingerprint = str(voice_profile.get("source_sha256", "0"))
        frequency = 180 + sum(fingerprint.encode("utf-8")) % 80
        samples = array(
            "h",
            (
                round(3_500 * math.sin(2 * math.pi * frequency * index / sample_rate))
                for index in range(round(duration * sample_rate))
            ),
        )
        return TTSAudio(samples.tobytes(), sample_rate)

    def unload(self) -> None:
        self.loaded = False
