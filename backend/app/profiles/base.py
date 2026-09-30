from typing import Any, Protocol

from app.audio.types import AudioArtifact


class VoiceProfileBuilder(Protocol):
    model_key: str

    async def build(self, artifact: AudioArtifact) -> dict[str, Any]:
        """Create model-specific profile data from a validated sample."""
        ...


class MockVoiceProfileBuilder:
    model_key = "mock-universal-v1"

    async def build(self, artifact: AudioArtifact) -> dict[str, Any]:
        return {
            "builder": "mock",
            "source_sha256": artifact.sha256,
            "sample_rate": artifact.preprocessing.target_sample_rate,
        }

