import hashlib
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.audio.ffmpeg import FFmpegMediaTool
from app.audio.preprocessing import AudioPreprocessingPipeline
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.metrics import MetricsRegistry
from app.core.rate_limit import FixedWindowRateLimiter
from app.db.session import create_session_factory
from app.models import build_model_registry
from app.profiles import ProfileBuilderRegistry, build_profile_builder_registry
from app.queue import CeleryJobQueue, JobQueue
from app.services.file_service import FileService
from app.services.user_service import UserDeletionPendingError
from app.storage import LocalObjectStorage


def _route_template(request: Request) -> str:
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if route != "unmatched" and not route.startswith("/api"):
        return f"/api{route}"
    return route


def create_app(
    settings: Settings | None = None,
    *,
    job_queue: JobQueue | None = None,
    file_service: FileService | None = None,
    profile_builder_registry: ProfileBuilderRegistry | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    if (
        runtime_settings.app_env == "production"
        and runtime_settings.auth_mode == "development_header"
    ):
        raise ValueError("production requires AUTH_MODE=trusted_proxy")
    if runtime_settings.auth_mode == "trusted_proxy":
        proxy_secret = runtime_settings.trusted_proxy_secret
        if proxy_secret is None or len(proxy_secret.get_secret_value()) < 32:
            raise ValueError(
                "trusted proxy authentication requires a 32+ character TRUSTED_PROXY_SECRET"
            )
    configure_logging(runtime_settings.log_level)
    log = structlog.get_logger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine, session_factory = create_session_factory(runtime_settings.database_url)
        app.state.settings = runtime_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.model_registry = build_model_registry(
            include_mock=runtime_settings.use_mock_inference,
            include_cosyvoice3=runtime_settings.enable_cosyvoice3,
        )
        app.state.profile_builder_registry = (
            profile_builder_registry
            or build_profile_builder_registry(
                include_mock=runtime_settings.use_mock_inference,
                include_cosyvoice3=runtime_settings.enable_cosyvoice3,
            )
        )
        app.state.file_service = file_service or FileService(
            LocalObjectStorage(runtime_settings.storage_path),
            AudioPreprocessingPipeline(
                FFmpegMediaTool(
                    ffmpeg_path=runtime_settings.ffmpeg_path,
                    ffprobe_path=runtime_settings.ffprobe_path,
                )
            ),
            max_upload_size=runtime_settings.max_upload_size,
            max_duration_seconds=runtime_settings.max_audio_duration_seconds,
        )
        app.state.job_queue = job_queue or CeleryJobQueue()
        log.info("api_started", environment=runtime_settings.app_env)
        yield
        await engine.dispose()
        log.info("api_stopped")

    app = FastAPI(
        title=runtime_settings.app_name,
        version=runtime_settings.app_version,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
    )
    app.state.metrics = MetricsRegistry()
    app.state.rate_limiter = FixedWindowRateLimiter(runtime_settings.rate_limit_requests_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "Last-Event-ID",
            "X-Request-ID",
            "X-User-ID",
        ],
    )

    @app.exception_handler(UserDeletionPendingError)
    async def deletion_pending_handler(
        _request: Request, _error: UserDeletionPendingError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=410,
            content={"detail": "계정 데이터 삭제가 진행 중입니다."},
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        supplied_request_id = request.headers.get("X-Request-ID", "")
        request_id = supplied_request_id if 0 < len(supplied_request_id) <= 128 else str(uuid4())
        traceparent = request.headers.get("traceparent", "")
        match = re.fullmatch(
            r"[\da-f]{2}-([\da-f]{32})-[\da-f]{16}-[\da-f]{2}", traceparent.lower()
        )
        trace_id = match.group(1) if match else uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, trace_id=trace_id)
        identity = request.headers.get("X-Authenticated-User") or request.headers.get("X-User-ID")
        remote = request.client.host if request.client else "unknown"
        rate_key = identity or remote
        control_path = request.url.path in {"/api/health", "/api/ready", "/api/metrics"}
        allowed, retry_after = (
            (True, 0) if control_path else request.app.state.rate_limiter.allow(rate_key)
        )
        if not allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
                headers={"Retry-After": str(retry_after)},
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                route = _route_template(request)
                duration = time.monotonic() - started
                request.app.state.metrics.observe_request(request.method, route, 500, duration)
                log.exception(
                    "http_request_failed",
                    method=request.method,
                    route=route,
                    duration_ms=round(duration * 1000, 3),
                )
                raise
        route = _route_template(request)
        duration = time.monotonic() - started
        request.app.state.metrics.observe_request(
            request.method, route, response.status_code, duration
        )
        log.info(
            "http_request_completed",
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 3),
            actor_hash=(hashlib.sha256(identity.encode()).hexdigest()[:16] if identity else None),
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(api_router)
    return app


app = create_app()
