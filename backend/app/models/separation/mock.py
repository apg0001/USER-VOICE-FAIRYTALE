from array import array

from app.models.base import ModelCapability, ModelDescriptor
from app.models.separation.base import SeparatedAudio


class MockSeparationModel:
    descriptor = ModelDescriptor(
        key="mock-separator-v1",
        display_name="Mock Vocal Separator",
        version="1.0.0",
        capabilities=(ModelCapability.SINGING_VOICE_CONVERSION,),
        requires_gpu=False,
        is_mock=True,
        metadata={"stems": ["vocal", "instrumental"]},
    )

    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def separate(self, pcm_s16le: bytes, sample_rate: int, channels: int) -> SeparatedAudio:
        if not self.loaded:
            raise RuntimeError("separator must be loaded before inference")
        source = array("h")
        source.frombytes(pcm_s16le)
        vocal = array("h")
        instrumental = array("h")
        for sample in source:
            vocal_sample = round(sample * 0.6)
            vocal.append(vocal_sample)
            instrumental.append(sample - vocal_sample)
        return SeparatedAudio(vocal.tobytes(), instrumental.tobytes())

    def unload(self) -> None:
        self.loaded = False
