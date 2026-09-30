from app.models.manager import ModelManager
from app.models.mock import MockVoiceModel
from app.models.registry import ModelRegistry


def build_model_registry(*, include_mock: bool) -> ModelRegistry:
    registry = ModelRegistry()
    if include_mock:
        registry.register(MockVoiceModel)
    return registry


__all__ = ["ModelManager", "ModelRegistry", "build_model_registry"]

