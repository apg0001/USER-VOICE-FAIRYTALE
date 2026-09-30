from fastapi import APIRouter, Request, Response, status
from fastapi.responses import PlainTextResponse
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from app.api.schemas import HealthResponse, ReadinessResponse
from app.db.models import InputArtifact, Job, User, VoiceProfile

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    checks: dict[str, str] = {}
    try:
        async with request.app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        await request.app.state.file_service.storage.healthcheck()
        checks["storage"] = "ok"
    except Exception:
        checks["storage"] = "unavailable"
    if request.app.state.settings.readiness_require_redis:
        client = Redis.from_url(request.app.state.settings.redis_url)
        try:
            await client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
        finally:
            await client.aclose()
    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", checks=checks)
    return ReadinessResponse(status="ready", checks=checks)


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics(request: Request) -> PlainTextResponse:
    operational_lines = [
        "# HELP voice_jobs Current jobs by durable status.",
        "# TYPE voice_jobs gauge",
    ]
    try:
        async with request.app.state.session_factory() as session:
            job_counts = list(
                await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))
            )
            retrying_inputs = await session.scalar(
                select(func.count(InputArtifact.id)).where(
                    InputArtifact.deletion_attempts > 0
                )
            )
            pending_profiles = await session.scalar(
                select(func.count(VoiceProfile.id)).where(
                    VoiceProfile.status == "DELETION_PENDING"
                )
            )
            pending_users = await session.scalar(
                select(func.count(User.id)).where(User.deletion_requested_at.is_not(None))
            )
        operational_lines.extend(
            f'voice_jobs{{status="{job_status.value}"}} {count}'
            for job_status, count in job_counts
        )
        operational_lines.extend(
            [
                "# HELP voice_cleanup_pending Pending deletion records.",
                "# TYPE voice_cleanup_pending gauge",
                f'voice_cleanup_pending{{kind="input_retry"}} {retrying_inputs or 0}',
                f'voice_cleanup_pending{{kind="profile"}} {pending_profiles or 0}',
                f'voice_cleanup_pending{{kind="user"}} {pending_users or 0}',
            ]
        )
    except Exception:
        operational_lines.extend(
            [
                "# HELP voice_metrics_collection_error Metrics DB collection failure.",
                "# TYPE voice_metrics_collection_error gauge",
                "voice_metrics_collection_error 1",
            ]
        )
    return PlainTextResponse(
        request.app.state.metrics.render() + "\n".join(operational_lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

