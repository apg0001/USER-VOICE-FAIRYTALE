from app.models.manager import ModelManager
from app.models.mock import MockVoiceModel
from app.models.registry import ModelRegistry
from app.models.singing.seed_vc import SEED_VC_DESCRIPTOR
from app.models.tts.cosyvoice import COSYVOICE3_DESCRIPTOR


def build_model_registry(
    *, include_mock: bool, include_cosyvoice3: bool = False, include_seed_vc: bool = False
) -> ModelRegistry:
    registry = ModelRegistry()
    if include_mock:
        registry.register(MockVoiceModel)
    if include_cosyvoice3:
        registry.register_descriptor(COSYVOICE3_DESCRIPTOR)
    if include_seed_vc:
        registry.register_descriptor(SEED_VC_DESCRIPTOR)
    return registry


__all__ = ["ModelManager", "ModelRegistry", "build_model_registry"]
