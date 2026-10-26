from typing import Any, Protocol

from app.audio.types import AudioArtifact


class VoiceProfileBuilder(Protocol):
    model_key: str

    async def build(
        self, artifact: AudioArtifact, *, sample_transcript: str | None = None
    ) -> dict[str, Any]:
        """Create model-specific profile data from a validated sample."""
        ...


class MockVoiceProfileBuilder:
    model_key = "mock-universal-v1"

    async def build(
        self, artifact: AudioArtifact, *, sample_transcript: str | None = None
    ) -> dict[str, Any]:
        return {
            "builder": "mock",
            "source_sha256": artifact.sha256,
            "sample_rate": artifact.preprocessing.target_sample_rate,
        }


class CosyVoice3ProfileBuilder:
    model_key = "cosyvoice3-0.5b-2512"

    async def build(
        self, artifact: AudioArtifact, *, sample_transcript: str | None = None
    ) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "builder": "cosyvoice3-reference-v1",
            "reference_storage_key": artifact.cleaned_storage_key,
            "source_sha256": artifact.sha256,
            "sample_rate": artifact.preprocessing.target_sample_rate,
        }
        if sample_transcript and sample_transcript.strip():
            profile["prompt_text"] = sample_transcript.strip()
        return profile
