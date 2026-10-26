import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_actor_id
from app.api.jobs.routes import serialize_job
from app.db.models import JobStatus
from app.queue import JobQueue
from app.services.job_service import JobNotFoundError, JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])

TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}


def _encode_event(*, event_id: int, event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


async def _job_snapshot(
    request: Request,
    actor_id: str,
    job_id: str,
) -> tuple[dict[str, object], JobStatus]:
    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    queue = cast(JobQueue, request.app.state.job_queue)
    async with factory() as session:
        service = JobService(session, queue)
        job = await service.get_owned_job(actor_id, job_id)
        response = await serialize_job(service, job)
        return response.model_dump(mode="json"), job.status


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    actor_id: Annotated[str, Depends(get_actor_id)],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
) -> StreamingResponse:
    try:
        await _job_snapshot(request, actor_id, job_id)
    except JobNotFoundError as error:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.") from error

    settings = request.app.state.settings

    async def events() -> AsyncIterator[str]:
        event_id = (last_event_id or 0) + 1
        previous_payload: dict[str, object] | None = None
        last_emit = time.monotonic()
        yield "retry: 2000\n\n"
        # StreamingResponse cancels this generator when the client disconnects.
        # Polling request.is_disconnected() here is unreliable behind HTTP middleware and
        # can terminate an otherwise active stream before the next durable snapshot.
        while True:
            try:
                payload, job_status = await _job_snapshot(request, actor_id, job_id)
            except JobNotFoundError:
                yield _encode_event(
                    event_id=event_id,
                    event="error",
                    data={"code": "JOB_NOT_FOUND"},
                )
                return
            if payload != previous_payload:
                yield _encode_event(event_id=event_id, event="job", data=payload)
                previous_payload = payload
                event_id += 1
                last_emit = time.monotonic()
            if job_status in TERMINAL_STATUSES:
                return
            if time.monotonic() - last_emit >= settings.sse_heartbeat_seconds:
                yield ": heartbeat\n\n"
                last_emit = time.monotonic()
            await asyncio.sleep(settings.sse_poll_interval_seconds)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
