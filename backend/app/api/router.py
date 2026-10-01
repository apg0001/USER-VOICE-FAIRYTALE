from fastapi import APIRouter

from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.jobs.events import router as job_events_router
from app.api.jobs.routes import router as jobs_router
from app.api.models import router as models_router
from app.api.users import router as users_router
from app.api.voices.routes import router as voices_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(models_router)
api_router.include_router(jobs_router)
api_router.include_router(job_events_router)
api_router.include_router(voices_router)
api_router.include_router(files_router)
api_router.include_router(users_router)
