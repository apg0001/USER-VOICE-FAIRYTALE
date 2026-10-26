import asyncio
import json

import structlog

from app.core.config import get_settings
from app.db.session import create_session_factory
from app.queue import CeleryJobQueue
from app.services.cleanup_service import CleanupService
from app.storage import LocalObjectStorage
from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


async def execute_cleanup() -> dict[str, int]:
    settings = get_settings()
    engine, session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory() as session:
            report = await CleanupService(
                session,
                LocalObjectStorage(settings.storage_path),
                CeleryJobQueue(),
                batch_size=settings.cleanup_batch_size,
                output_retention_hours=settings.output_retention_hours,
                orphan_grace_hours=settings.orphan_grace_hours,
            ).cleanup_expired()
            return report.to_dict()
    finally:
        await engine.dispose()


@celery_app.task(name="voice.cleanup_expired")  # type: ignore[untyped-decorator]
def cleanup_expired() -> dict[str, int]:
    log.info("retention_cleanup_started")
    return asyncio.run(execute_cleanup())


if __name__ == "__main__":
    print(json.dumps(asyncio.run(execute_cleanup()), sort_keys=True))
