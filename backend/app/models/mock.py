from typing import Any

from app.models.base import ModelCapability, ModelDescriptor, VoiceModel


class MockVoiceModel(VoiceModel):
    """Dependency-light adapter used for local development and CI."""

    descriptor = ModelDescriptor(
        key="mock-universal-v1",
        display_name="Mock Universal Voice Model",
        version="1.0.0",
        capabilities=tuple(ModelCapability),
        requires_gpu=False,
        is_mock=True,
        metadata={"purpose": "development-and-contract-tests"},
    )

    def __init__(self) -> None:
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def preprocess(self, input_data: Any) -> Any:
        return input_data

    def infer(self, input_data: Any, voice_profile: dict[str, Any]) -> dict[str, Any]:
        if not self.loaded:
            raise RuntimeError("model must be loaded before inference")
        return {"input": input_data, "voice_profile": voice_profile, "mock": True}

    def postprocess(self, output: Any) -> Any:
        return output

    def unload(self) -> None:
        self.loaded = False
