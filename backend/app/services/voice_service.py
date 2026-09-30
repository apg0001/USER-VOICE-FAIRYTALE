from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.types import AudioArtifact, PreprocessingConfig
from app.db.models import VoiceProfile, VoiceSample
from app.profiles import ProfileBuilderRegistry
from app.services.file_service import FileService
from app.services.user_service import resolve_user


class VoiceProfileNotFoundError(LookupError):
    pass


class ConsentRequiredError(ValueError):
    pass


class InsufficientVoiceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceConsent:
    accepted: bool
    owns_voice_or_has_permission: bool
    version: str


class VoiceService:
    def __init__(
        self,
        session: AsyncSession,
        file_service: FileService,
        builder_registry: ProfileBuilderRegistry,
        *,
        required_consent_version: str,
        min_speech_seconds: float,
    ) -> None:
        self.session = session
        self.file_service = file_service
        self.builder_registry = builder_registry
        self.required_consent_version = required_consent_version
        self.min_speech_seconds = min_speech_seconds

    async def create_profile(
        self,
        external_user_id: str,
        *,
        name: str,
        source: Path,
        original_filename: str,
        content_type: str,
        consent: VoiceConsent,
        preprocessing: PreprocessingConfig,
    ) -> VoiceProfile:
        if (
            not consent.accepted
            or not consent.owns_voice_or_has_permission
            or consent.version != self.required_consent_version
        ):
            raise ConsentRequiredError

        user = await resolve_user(self.session, external_user_id, create=True)
        assert user is not None
        profile = VoiceProfile(
            user_id=user.id,
            name=name,
            status="PROCESSING",
            consent_version=consent.version,
            consented_at=datetime.now(UTC),
            profile_metadata={
                "consent": {
                    "accepted": True,
                    "ownership_declared": True,
                    "version": consent.version,
                }
            },
        )
        self.session.add(profile)
        await self.session.commit()
        await self.session.refresh(profile)

        artifact: AudioArtifact | None = None
        try:
            artifact = await self.file_service.ingest_audio(
                source,
                original_filename=original_filename,
                content_type=content_type,
                namespace=f"voice-profiles/{profile.id}",
                config=preprocessing,
            )
            if artifact.quality.speech_seconds < self.min_speech_seconds:
                raise InsufficientVoiceError
            model_profiles = await self.builder_registry.build_all(artifact)
            sample = VoiceSample(
                voice_profile_id=profile.id,
                original_storage_key=artifact.original_storage_key,
                cleaned_storage_key=artifact.cleaned_storage_key,
                duration_seconds=artifact.probe.duration_seconds,
                sample_rate=artifact.preprocessing.target_sample_rate,
                quality_metadata=artifact.quality.to_dict(),
            )
            self.session.add(sample)
            profile.status = "READY"
            profile.profile_metadata = {
                **profile.profile_metadata,
                "preprocessing": artifact.preprocessing.to_dict(),
                "probe": artifact.probe.to_dict(),
                "model_profiles": model_profiles,
                "sample_sha256": artifact.sha256,
            }
            await self.session.commit()
            await self.session.refresh(profile)
            return profile
        except Exception:
            if artifact is not None:
                await self.file_service.storage.delete(artifact.cleaned_storage_key)
                await self.file_service.storage.delete(artifact.original_storage_key)
            profile.status = "REJECTED"
            await self.session.commit()
            raise

    async def list_profiles(self, external_user_id: str) -> list[VoiceProfile]:
        user = await resolve_user(self.session, external_user_id, create=False)
        if user is None:
            return []
        return list(
            await self.session.scalars(
                select(VoiceProfile)
                .where(VoiceProfile.user_id == user.id)
                .order_by(VoiceProfile.created_at.desc())
            )
        )

    async def get_profile(self, external_user_id: str, profile_id: str) -> VoiceProfile:
        user = await resolve_user(self.session, external_user_id, create=False)
        if user is None:
            raise VoiceProfileNotFoundError(profile_id)
        profile = await self.session.scalar(
            select(VoiceProfile).where(
                VoiceProfile.id == profile_id,
                VoiceProfile.user_id == user.id,
            )
        )
        if profile is None:
            raise VoiceProfileNotFoundError(profile_id)
        return profile

    async def delete_profile(self, external_user_id: str, profile_id: str) -> None:
        profile = await self.get_profile(external_user_id, profile_id)
        samples = list(
            await self.session.scalars(
                select(VoiceSample).where(VoiceSample.voice_profile_id == profile.id)
            )
        )
        profile.status = "DELETION_PENDING"
        await self.session.commit()
        for sample in samples:
            if sample.cleaned_storage_key:
                await self.file_service.storage.delete(sample.cleaned_storage_key)
            await self.file_service.storage.delete(sample.original_storage_key)
            await self.session.delete(sample)
        await self.session.delete(profile)
        await self.session.commit()

