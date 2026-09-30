import asyncio
from datetime import UTC, datetime

from conftest import RecordingQueue
from fastapi.testclient import TestClient

from app.db.models import VoiceProfile
from app.services.user_service import resolve_user

HEADERS = {"X-User-ID": "user-alice", "Idempotency-Key": "request-00000001"}
PAYLOAD = {
    "mode": "general_tts",
    "model_key": "mock-universal-v1",
    "input_text": "옛날 옛적에 작은 마을이 있었습니다.",
    "request_config": {},
}


def test_create_is_idempotent_and_owned(
    job_client: TestClient, recording_queue: RecordingQueue
) -> None:
    first = job_client.post("/api/jobs", headers=HEADERS, json=PAYLOAD)
    repeated = job_client.post("/api/jobs", headers=HEADERS, json=PAYLOAD)

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["status"] == "QUEUED"
    assert first.json()["queue_position"] == 1
    assert recording_queue.enqueued == [first.json()["id"]]

    forbidden = job_client.get(
        f"/api/jobs/{first.json()['id']}", headers={"X-User-ID": "user-bob"}
    )
    assert forbidden.status_code == 404


def test_list_cancel_and_retry(job_client: TestClient, recording_queue: RecordingQueue) -> None:
    created = job_client.post("/api/jobs", headers=HEADERS, json=PAYLOAD).json()

    listing = job_client.get("/api/jobs", headers={"X-User-ID": "user-alice"})
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    cancelled = job_client.post(
        f"/api/jobs/{created['id']}/cancel", headers={"X-User-ID": "user-alice"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert recording_queue.revoked == [created["id"]]

    retried = job_client.post(
        f"/api/jobs/{created['id']}/retry", headers={"X-User-ID": "user-alice"}
    )
    assert retried.status_code == 202
    assert retried.json()["attempt"] == 2
    assert retried.json()["retry_of_job_id"] == created["id"]
    assert retried.json()["id"] != created["id"]


def test_job_input_and_model_are_validated(job_client: TestClient) -> None:
    missing_text = job_client.post(
        "/api/jobs",
        headers={"X-User-ID": "user-alice"},
        json={"mode": "general_tts", "model_key": "mock-universal-v1"},
    )
    wrong_model = job_client.post(
        "/api/jobs",
        headers={"X-User-ID": "user-alice"},
        json={**PAYLOAD, "model_key": "unknown"},
    )

    assert missing_text.status_code == 422
    assert wrong_model.status_code == 422


def test_job_rejects_another_users_or_unready_voice_profile(
    job_client: TestClient,
) -> None:
    async def seed_profile(owner: str, status: str) -> str:
        async with job_client.app.state.session_factory() as session:
            user = await resolve_user(session, owner, create=True)
            assert user is not None
            profile = VoiceProfile(
                user_id=user.id,
                name=f"{owner} voice",
                status=status,
                consent_version="2026-09-01",
                consented_at=datetime.now(UTC),
                profile_metadata={},
            )
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            return profile.id

    other_users_profile_id = asyncio.run(seed_profile("user-bob", "READY"))
    unready_profile_id = asyncio.run(seed_profile("user-alice", "PROCESSING"))

    other_users_profile = job_client.post(
        "/api/jobs",
        headers={**HEADERS, "Idempotency-Key": "request-00000002"},
        json={**PAYLOAD, "voice_profile_id": other_users_profile_id},
    )
    unready_profile = job_client.post(
        "/api/jobs",
        headers={**HEADERS, "Idempotency-Key": "request-00000003"},
        json={**PAYLOAD, "voice_profile_id": unready_profile_id},
    )

    assert other_users_profile.status_code == 422
    assert unready_profile.status_code == 422

