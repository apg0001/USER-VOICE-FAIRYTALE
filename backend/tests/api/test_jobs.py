import asyncio
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from conftest import RecordingQueue
from fastapi.testclient import TestClient

from app.db.models import JobOutput, JobStatus, VoiceProfile
from app.services.job_service import JobService
from app.services.user_service import resolve_user

HEADERS = {"X-User-ID": "user-alice", "Idempotency-Key": "request-00000001"}
PAYLOAD = {
    "mode": "general_tts",
    "model_key": "mock-universal-v1",
    "input_text": "옛날 옛적에 작은 마을이 있었습니다.",
    "request_config": {},
}


def seed_profile(
    job_client: TestClient,
    owner: str,
    status: str = "READY",
    *,
    include_mock_profile: bool = True,
) -> str:
    async def create() -> str:
        async with job_client.app.state.session_factory() as session:
            user = await resolve_user(session, owner, create=True)
            assert user is not None
            profile = VoiceProfile(
                user_id=user.id,
                name=f"{owner} voice",
                status=status,
                consent_version="2026-09-01",
                consented_at=datetime.now(UTC),
                profile_metadata={
                    "model_profiles": (
                        {"mock-universal-v1": {"source_sha256": "a" * 64}}
                        if include_mock_profile
                        else {}
                    )
                },
            )
            session.add(profile)
            await session.commit()
            await session.refresh(profile)
            return profile.id

    return asyncio.run(create())


def tts_payload(profile_id: str) -> dict[str, object]:
    return {**PAYLOAD, "voice_profile_id": profile_id}


def test_create_is_idempotent_and_owned(
    job_client: TestClient, recording_queue: RecordingQueue
) -> None:
    profile_id = seed_profile(job_client, "user-alice")
    first = job_client.post("/api/jobs", headers=HEADERS, json=tts_payload(profile_id))
    repeated = job_client.post("/api/jobs", headers=HEADERS, json=tts_payload(profile_id))

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert first.json()["id"] == repeated.json()["id"]
    assert first.json()["status"] == "QUEUED"
    assert first.json()["queue_position"] == 1
    assert recording_queue.enqueued == [first.json()["id"]]

    forbidden = job_client.get(f"/api/jobs/{first.json()['id']}", headers={"X-User-ID": "user-bob"})
    assert forbidden.status_code == 404


def test_list_cancel_and_retry(job_client: TestClient, recording_queue: RecordingQueue) -> None:
    profile_id = seed_profile(job_client, "user-alice")
    created = job_client.post("/api/jobs", headers=HEADERS, json=tts_payload(profile_id)).json()

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
    profile_id = seed_profile(job_client, "user-alice")
    missing_profile = job_client.post(
        "/api/jobs",
        headers=HEADERS,
        json=PAYLOAD,
    )
    missing_text = job_client.post(
        "/api/jobs",
        headers={"X-User-ID": "user-alice"},
        json={
            "mode": "general_tts",
            "model_key": "mock-universal-v1",
            "voice_profile_id": profile_id,
        },
    )
    wrong_model = job_client.post(
        "/api/jobs",
        headers={"X-User-ID": "user-alice"},
        json={**tts_payload(profile_id), "model_key": "unknown"},
    )

    assert missing_profile.status_code == 422
    assert missing_text.status_code == 422
    assert wrong_model.status_code == 422


def test_job_rejects_another_users_or_unready_voice_profile(
    job_client: TestClient,
) -> None:
    other_users_profile_id = seed_profile(job_client, "user-bob")
    unready_profile_id = seed_profile(job_client, "user-alice", "PROCESSING")

    other_users_profile = job_client.post(
        "/api/jobs",
        headers={**HEADERS, "Idempotency-Key": "request-00000002"},
        json=tts_payload(other_users_profile_id),
    )
    unready_profile = job_client.post(
        "/api/jobs",
        headers={**HEADERS, "Idempotency-Key": "request-00000003"},
        json=tts_payload(unready_profile_id),
    )

    assert other_users_profile.status_code == 422
    assert unready_profile.status_code == 422


def test_job_rejects_profile_without_selected_model_data(
    job_client: TestClient,
) -> None:
    profile_id = seed_profile(job_client, "user-alice", include_mock_profile=False)

    response = job_client.post(
        "/api/jobs",
        headers={**HEADERS, "Idempotency-Key": "request-model-profile-gap"},
        json=tts_payload(profile_id),
    )

    assert response.status_code == 422


def test_job_output_is_listed_and_owner_can_download(
    job_client: TestClient, tmp_path: Path
) -> None:
    profile_id = seed_profile(job_client, "user-alice")
    created = job_client.post("/api/jobs", headers=HEADERS, json=tts_payload(profile_id)).json()
    source = tmp_path / "result.wav"
    source.write_bytes(b"RIFFmock-wave")
    storage_key = asyncio.run(
        job_client.app.state.file_service.storage.put(
            source, namespace=f"jobs/{created['id']}/outputs", suffix="wav"
        )
    )

    async def seed_output() -> str:
        async with job_client.app.state.session_factory() as session:
            output = JobOutput(
                job_id=created["id"],
                storage_key=storage_key,
                content_type="audio/wav",
                duration_seconds=1.25,
                output_metadata={},
            )
            session.add(output)
            await session.commit()
            await session.refresh(output)
            return output.id

    output_id = asyncio.run(seed_output())
    detail = job_client.get(f"/api/jobs/{created['id']}", headers={"X-User-ID": "user-alice"})
    downloaded = job_client.get(f"/api/files/{output_id}", headers={"X-User-ID": "user-alice"})
    hidden = job_client.get(f"/api/files/{output_id}", headers={"X-User-ID": "user-bob"})

    assert detail.json()["outputs"][0]["download_url"] == f"/api/files/{output_id}"
    assert downloaded.status_code == 200
    assert downloaded.content == b"RIFFmock-wave"
    assert hidden.status_code == 404


def test_terminal_job_event_stream_is_owned_and_resumes_event_id(
    job_client: TestClient,
) -> None:
    profile_id = seed_profile(job_client, "user-alice")
    created = job_client.post("/api/jobs", headers=HEADERS, json=tts_payload(profile_id)).json()

    async def complete_job() -> None:
        async with job_client.app.state.session_factory() as session:
            job = await JobService(session, RecordingQueue()).get_job(created["id"])
            job.status = JobStatus.COMPLETED
            job.progress = 100
            job.estimated_remaining_seconds = 0
            await session.commit()

    asyncio.run(complete_job())
    with job_client.stream(
        "GET",
        f"/api/jobs/{created['id']}/events",
        headers={"X-User-ID": "user-alice", "Last-Event-ID": "41"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "retry: 2000" in body
    assert "id: 42" in body
    assert "event: job" in body
    data_line = next(line for line in body.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["status"] == "COMPLETED"

    hidden = job_client.get(
        f"/api/jobs/{created['id']}/events",
        headers={"X-User-ID": "user-bob"},
    )
    assert hidden.status_code == 404


def test_job_event_stream_emits_heartbeat_and_changed_terminal_state(
    job_client: TestClient,
) -> None:
    profile_id = seed_profile(job_client, "user-alice")
    created = job_client.post("/api/jobs", headers=HEADERS, json=tts_payload(profile_id)).json()

    def finish_later() -> None:
        time.sleep(0.06)

        async def cancel() -> None:
            async with job_client.app.state.session_factory() as session:
                job = await JobService(session, RecordingQueue()).get_job(created["id"])
                job.status = JobStatus.CANCELLED
                job.cancel_requested = True
                await session.commit()

        asyncio.run(cancel())

    updater = threading.Thread(target=finish_later)
    updater.start()
    with job_client.stream(
        "GET",
        f"/api/jobs/{created['id']}/events",
        headers={"X-User-ID": "user-alice"},
    ) as response:
        body = "".join(response.iter_text())
    updater.join(timeout=1)

    assert response.status_code == 200
    assert ": heartbeat" in body
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    assert [payload["status"] for payload in payloads] == ["QUEUED", "CANCELLED"]
