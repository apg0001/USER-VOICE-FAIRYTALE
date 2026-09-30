from app.profiles.base import MockVoiceProfileBuilder
from app.profiles.registry import ProfileBuilderRegistry


def build_profile_builder_registry(*, include_mock: bool) -> ProfileBuilderRegistry:
    registry = ProfileBuilderRegistry()
    if include_mock:
        registry.register(MockVoiceProfileBuilder())
    return registry


__all__ = ["ProfileBuilderRegistry", "build_profile_builder_registry"]

