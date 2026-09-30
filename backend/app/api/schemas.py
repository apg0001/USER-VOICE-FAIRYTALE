from pydantic import BaseModel, ConfigDict

from app.models.base import ModelCapability


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    display_name: str
    version: str
    capabilities: tuple[ModelCapability, ...]
    requires_gpu: bool
    is_mock: bool


class ModelListResponse(BaseModel):
    items: list[ModelResponse]

