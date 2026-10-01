from app.profiles.base import CosyVoice3ProfileBuilder, MockVoiceProfileBuilder
from app.profiles.registry import ProfileBuilderRegistry


def build_profile_builder_registry(
    *, include_mock: bool, include_cosyvoice3: bool = False
) -> ProfileBuilderRegistry:
    registry = ProfileBuilderRegistry()
    if include_mock:
        registry.register(MockVoiceProfileBuilder())
    if include_cosyvoice3:
        registry.register(CosyVoice3ProfileBuilder())
    return registry


__all__ = ["ProfileBuilderRegistry", "build_profile_builder_registry"]
