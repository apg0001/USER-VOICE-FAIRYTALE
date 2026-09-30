from app.audio.types import AudioArtifact
from app.profiles.base import VoiceProfileBuilder


class ProfileBuilderRegistry:
    def __init__(self) -> None:
        self._builders: dict[str, VoiceProfileBuilder] = {}

    def register(self, builder: VoiceProfileBuilder) -> None:
        if builder.model_key in self._builders:
            raise ValueError(f"profile builder already registered: {builder.model_key}")
        self._builders[builder.model_key] = builder

    async def build_all(self, artifact: AudioArtifact) -> dict[str, object]:
        return {
            model_key: await builder.build(artifact)
            for model_key, builder in self._builders.items()
        }

