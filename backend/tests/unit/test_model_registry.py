import pytest

from app.core.config import Settings
from app.models import build_model_registry
from app.models.base import ModelCapability
from app.models.mock import MockVoiceModel
from app.models.registry import ModelNotFoundError, ModelRegistry
from app.models.tts.cosyvoice import COSYVOICE3_DESCRIPTOR, CosyVoice3TTSModel
from app.models.tts.registry import build_tts_model_registry


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


def test_registry_can_publish_worker_only_descriptor() -> None:
    registry = ModelRegistry()
    registry.register_descriptor(COSYVOICE3_DESCRIPTOR)

    assert tuple(registry.descriptors()) == (COSYVOICE3_DESCRIPTOR,)
    with pytest.raises(ModelNotFoundError):
        registry.create(COSYVOICE3_DESCRIPTOR.key)


def test_real_tts_descriptor_and_factory_are_opt_in() -> None:
    disabled = build_model_registry(include_mock=False)
    enabled = build_model_registry(include_mock=False, include_cosyvoice3=True)
    settings = Settings(app_env="test", enable_cosyvoice3=True)
    worker_registry = build_tts_model_registry(include_mock=False, settings=settings)

    assert tuple(disabled.descriptors()) == ()
    assert tuple(enabled.descriptors()) == (COSYVOICE3_DESCRIPTOR,)
    assert isinstance(worker_registry.create(COSYVOICE3_DESCRIPTOR.key), CosyVoice3TTSModel)
