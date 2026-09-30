from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import JobMode, JobStatus


class JobCreateRequest(BaseModel):
    mode: JobMode
    model_key: str = Field(min_length=1, max_length=120)
    voice_profile_id: str | None = Field(default=None, max_length=36)
    input_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    input_storage_key: str | None = Field(default=None, max_length=512)
    request_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mode_input(self) -> "JobCreateRequest":
        if self.mode in {JobMode.GENERAL_TTS, JobMode.LONG_FORM_TTS} and not self.input_text:
            raise ValueError("TTS 작업에는 input_text가 필요합니다.")
        if self.mode in {
            JobMode.SPEECH_VOICE_CONVERSION,
            JobMode.SINGING_VOICE_CONVERSION,
        } and not self.input_storage_key:
            raise ValueError("음성 변환 작업에는 input_storage_key가 필요합니다.")
        return self


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    mode: JobMode
    status: JobStatus
    progress: int
    estimated_remaining_seconds: int | None
    queue_position: int | None = None
    voice_profile_id: str | None
    model_key: str
    attempt: int
    retry_of_job_id: str | None
    error_code: str | None
    user_message: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    limit: int
    offset: int

