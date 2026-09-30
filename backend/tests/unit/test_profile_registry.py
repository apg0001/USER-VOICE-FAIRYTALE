import pytest

from app.audio.types import (
    AudioArtifact,
    AudioProbe,
    AudioQuality,
    PreprocessingConfig,
)
from app.profiles.base import MockVoiceProfileBuilder
from app.profiles.registry import ProfileBuilderRegistry


@pytest.mark.asyncio
async def test_builder_registry_creates_model_specific_profile_data() -> None:
    registry = ProfileBuilderRegistry()
    registry.register(MockVoiceProfileBuilder())
    artifact = AudioArtifact(
        "original/key.wav",
        "cleaned/key.wav",
        "b" * 64,
        AudioProbe("wav", "pcm_s16le", 12, 24_000, 1, 100),
        AudioQuality(11, 1, 0.08, -2, False, -55, "low"),
        PreprocessingConfig(),
    )

    profiles = await registry.build_all(artifact)

    assert profiles["mock-universal-v1"] == {
        "builder": "mock",
        "source_sha256": "b" * 64,
        "sample_rate": 24_000,
    }


def test_builder_registry_rejects_duplicate_model_key() -> None:
    registry = ProfileBuilderRegistry()
    registry.register(MockVoiceProfileBuilder())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(MockVoiceProfileBuilder())

