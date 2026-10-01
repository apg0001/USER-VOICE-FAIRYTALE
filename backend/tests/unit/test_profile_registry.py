import pytest

from app.audio.types import (
    AudioArtifact,
    AudioProbe,
    AudioQuality,
    PreprocessingConfig,
)
from app.profiles.base import CosyVoice3ProfileBuilder, MockVoiceProfileBuilder
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


@pytest.mark.asyncio
async def test_cosyvoice_profile_preserves_reference_and_transcript() -> None:
    registry = ProfileBuilderRegistry()
    registry.register(CosyVoice3ProfileBuilder())
    artifact = AudioArtifact(
        "original/key.wav",
        "cleaned/key.wav",
        "c" * 64,
        AudioProbe("wav", "pcm_s16le", 12, 24_000, 1, 100),
        AudioQuality(11, 1, 0.08, -2, False, -55, "low"),
        PreprocessingConfig(),
    )

    profiles = await registry.build_all(
        artifact, sample_transcript="  정확히 읽은 한국어 문장입니다.  "
    )

    assert profiles["cosyvoice3-0.5b-2512"] == {
        "builder": "cosyvoice3-reference-v1",
        "reference_storage_key": "cleaned/key.wav",
        "source_sha256": "c" * 64,
        "sample_rate": 24_000,
        "prompt_text": "정확히 읽은 한국어 문장입니다.",
    }
