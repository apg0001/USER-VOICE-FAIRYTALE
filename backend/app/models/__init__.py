from app.models.mock import MockVoiceModel
from app.models.registry import ModelRegistry


def build_model_registry(*, include_mock: bool) -> ModelRegistry:
    registry = ModelRegistry()
    if include_mock:
        registry.register(MockVoiceModel)
    return registry


__all__ = ["ModelRegistry", "build_model_registry"]

