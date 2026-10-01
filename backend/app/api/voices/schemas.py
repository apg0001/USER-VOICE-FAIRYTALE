from datetime import datetime
from typing import Any

from pydantic import BaseModel


class VoiceSampleSummary(BaseModel):
    id: str
    duration_seconds: float | None
    sample_rate: int | None
    quality: dict[str, Any]


class VoiceProfileResponse(BaseModel):
    id: str
    name: str
    status: str
    consent_version: str
    consented_at: datetime
    created_at: datetime
    samples: list[VoiceSampleSummary]
    available_models: list[str]


class VoiceProfileListResponse(BaseModel):
    items: list[VoiceProfileResponse]


class VoiceConsentResponse(BaseModel):
    version: str
    statement: str
