import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDTimestampMixin


class JobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PREPROCESSING = "PREPROCESSING"
    LOADING_MODEL = "LOADING_MODEL"
    INFERENCE = "INFERENCE"
    POSTPROCESSING = "POSTPROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobMode(str, enum.Enum):
    GENERAL_TTS = "general_tts"
    LONG_FORM_TTS = "long_form_tts"
    SPEECH_VOICE_CONVERSION = "speech_voice_conversion"
    SINGING_VOICE_CONVERSION = "singing_voice_conversion"


class User(UUIDTimestampMixin, Base):
    __tablename__ = "users"

    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    voice_profiles: Mapped[list["VoiceProfile"]] = relationship(back_populates="user")
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")


class VoiceProfile(UUIDTimestampMixin, Base):
    __tablename__ = "voice_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    consent_version: Mapped[str] = mapped_column(String(30))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    profile_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="voice_profiles")
    samples: Mapped[list["VoiceSample"]] = relationship(back_populates="voice_profile")


class VoiceSample(UUIDTimestampMixin, Base):
    __tablename__ = "voice_samples"

    voice_profile_id: Mapped[str] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="CASCADE"), index=True
    )
    original_storage_key: Mapped[str] = mapped_column(String(512))
    cleaned_storage_key: Mapped[str | None] = mapped_column(String(512))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    sample_rate: Mapped[int | None] = mapped_column(Integer)
    quality_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    voice_profile: Mapped[VoiceProfile] = relationship(back_populates="samples")


class ModelRecord(UUIDTimestampMixin, Base):
    __tablename__ = "models"

    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    version: Mapped[str] = mapped_column(String(80))
    model_type: Mapped[str] = mapped_column(String(80), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Job(UUIDTimestampMixin, Base):
    __tablename__ = "jobs"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    voice_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="SET NULL"), index=True
    )
    mode: Mapped[JobMode] = mapped_column(
        Enum(
            JobMode,
            native_enum=False,
            values_callable=lambda items: [item.value for item in items],
        ),
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False), default=JobStatus.QUEUED, index=True
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)
    estimated_remaining_seconds: Mapped[int | None] = mapped_column(Integer)
    input_storage_key: Mapped[str | None] = mapped_column(String(512))
    input_text: Mapped[str | None] = mapped_column(Text)
    model_key: Mapped[str] = mapped_column(String(120))
    request_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="jobs")
    outputs: Mapped[list["JobOutput"]] = relationship(back_populates="job")


class JobOutput(UUIDTimestampMixin, Base):
    __tablename__ = "job_outputs"

    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(100), default="audio/wav")
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    output_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped[Job] = relationship(back_populates="outputs")

