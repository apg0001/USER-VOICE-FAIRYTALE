from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.schemas import ModelListResponse, ModelResponse
from app.models.base import ModelCapability

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelListResponse)
async def list_models(
    request: Request,
    capability: Annotated[ModelCapability | None, Query()] = None,
) -> ModelListResponse:
    registry = request.app.state.model_registry
    return ModelListResponse(
        items=[ModelResponse.model_validate(item) for item in registry.descriptors(capability)]
    )
