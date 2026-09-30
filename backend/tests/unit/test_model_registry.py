import pytest

from app.models.base import ModelCapability
from app.models.mock import MockVoiceModel
from app.models.registry import ModelNotFoundError, ModelRegistry


def test_registry_creates_registered_adapter() -> None:
    registry = ModelRegistry()
    registry.register(MockVoiceModel)

    model = registry.create("mock-universal-v1")

    assert isinstance(model, MockVoiceModel)
    assert len(tuple(registry.descriptors(ModelCapability.GENERAL_TTS))) == 1


def test_registry_rejects_unknown_model() -> None:
    with pytest.raises(ModelNotFoundError):
        ModelRegistry().create("missing")


def test_mock_requires_explicit_load() -> None:
    model = MockVoiceModel()
    with pytest.raises(RuntimeError, match="must be loaded"):
        model.infer("hello", {})

