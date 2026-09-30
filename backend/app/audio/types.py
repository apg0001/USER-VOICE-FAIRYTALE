from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class NoiseReduction(StrEnum):
    OFF = "off"
    NORMAL = "normal"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class AudioProbe:
    format_name: str
    codec_name: str
    duration_seconds: float
    sample_rate: int
    channels: int
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AudioQuality:
    speech_seconds: float
    silence_seconds: float
    silence_ratio: float
    peak_db: float | None
    clipping: bool
    noise_floor_db: float | None
    noise_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreprocessingConfig:
    target_sample_rate: int = 24_000
    trim_silence: bool = True
    normalize_loudness: bool = True
    noise_reduction: NoiseReduction = NoiseReduction.NORMAL

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["noise_reduction"] = self.noise_reduction.value
        return payload


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    original_storage_key: str
    cleaned_storage_key: str
    sha256: str
    probe: AudioProbe
    quality: AudioQuality
    preprocessing: PreprocessingConfig

