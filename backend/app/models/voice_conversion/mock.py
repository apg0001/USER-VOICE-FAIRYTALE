from array import array
from typing import Any

from app.models.base import ModelCapability, ModelDescriptor


class MockVoiceConversionModel:
    descriptor = ModelDescriptor(
        key="mock-universal-v1",
        display_name="Mock Universal Voice Model",
        version="1.0.0",
        capabilities=(ModelCapability.SPEECH_VOICE_CONVERSION,),
        requires_gpu=False,
        is_mock=True,
        metadata={"preserves": ["duration", "sample_rate", "timing"]},
    )

    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def convert(
        self, pcm_s16le: bytes, sample_rate: int, voice_profile: dict[str, Any]
    ) -> bytes:
        if not self.loaded:
            raise RuntimeError("model must be loaded before inference")
        fingerprint = str(voice_profile.get("source_sha256", "0"))
        gain = 0.85 + (sum(fingerprint.encode("utf-8")) % 20) / 100
        samples = array("h")
        samples.frombytes(pcm_s16le)
        for index, sample in enumerate(samples):
            samples[index] = max(-32_768, min(32_767, round(sample * gain)))
        return samples.tobytes()

    def unload(self) -> None:
        self.loaded = False
