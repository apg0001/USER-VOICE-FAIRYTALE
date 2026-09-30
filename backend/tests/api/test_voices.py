import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import RecordingQueue, create_test_schema
from fastapi.testclient import TestClient

from app.audio.types import (
    AudioArtifact,
    AudioProbe,
    AudioQuality,
    PreprocessingConfig,
)
from app.core.config import Settings
from app.main import create_app
from app.storage import LocalObjectStorage


class StubFileService:
    def __init__(self, root: Path, *, speech_seconds: float = 20.0) -> None:
        self.storage = LocalObjectStorage(root)
        self.speech_seconds = speech_seconds
        self.ingest_count = 0
        self.last_config: PreprocessingConfig | None = None

    async def ingest_audio(
        self,
        source: Path,
        *,
        original_filename: str,
        content_type: str,
        namespace: str,
        config: PreprocessingConfig,
    ) -> AudioArtifact:
        self.ingest_count += 1
        self.last_config = config
        original_key = await self.storage.put(
            source, namespace=f"{namespace}/original", suffix="wav"
        )
        cleaned_key = await self.storage.put(source, namespace=f"{namespace}/cleaned", suffix="wav")
        size = (await asyncio.to_thread(source.stat)).st_size
        return AudioArtifact(
            original_key,
            cleaned_key,
            "a" * 64,
            AudioProbe("wav", "pcm_s16le", 22.0, 24_000, 1, size),
            AudioQuality(
                self.speech_seconds,
                2.0,
                0.09,
                -1.0,
                False,
                -50.0,
                "low",
            ),
            config,
        )


@pytest.fixture
def voice_app(tmp_path: Path) -> Iterator[tuple[TestClient, StubFileService]]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'voices.db'}"
    asyncio.run(create_test_schema(database_url))
    settings = Settings(
        app_env="test",
        database_url=database_url,
        storage_path=tmp_path / "storage",
        model_path=tmp_path / "models",
        use_mock_inference=True,
        voice_consent_version="2026-09-01",
        min_voice_profile_speech_seconds=10,
    )
    file_service = StubFileService(tmp_path / "objects")
    with TestClient(
        create_app(
            settings,
            job_queue=RecordingQueue(),
            file_service=file_service,  # type: ignore[arg-type]
        )
    ) as client:
        yield client, file_service


def profile_form(*, consent: str = "true", version: str = "2026-09-01") -> dict[str, str]:
    return {
        "name": "내 이야기 목소리",
        "consent_accepted": consent,
        "owns_voice_or_has_permission": "true",
        "consent_version": version,
        "noise_reduction": "normal",
    }


def test_consent_is_required_before_audio_processing(
    voice_app: tuple[TestClient, StubFileService],
) -> None:
    client, file_service = voice_app
    response = client.post(
        "/api/voices",
        headers={"X-User-ID": "voice-owner"},
        data=profile_form(consent="false"),
        files={"voice_sample": ("voice.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    )

    assert response.status_code == 422
    assert file_service.ingest_count == 0


def test_current_consent_statement_is_discoverable(
    voice_app: tuple[TestClient, StubFileService],
) -> None:
    client, _ = voice_app
    response = client.get("/api/voices/consent")

    assert response.status_code == 200
    assert response.json()["version"] == "2026-09-01"
    assert "사용 권한" in response.json()["statement"]


def test_create_list_ownership_and_delete_profile(
    voice_app: tuple[TestClient, StubFileService],
) -> None:
    client, _ = voice_app
    created = client.post(
        "/api/voices",
        headers={"X-User-ID": "voice-owner"},
        data=profile_form(),
        files={"voice_sample": ("voice.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["status"] == "READY"
    assert payload["consent_version"] == "2026-09-01"
    assert payload["available_models"] == ["mock-universal-v1"]
    assert payload["samples"][0]["quality"]["speech_seconds"] == 20.0

    listing = client.get("/api/voices", headers={"X-User-ID": "voice-owner"})
    assert listing.status_code == 200
    assert len(listing.json()["items"]) == 1
    hidden = client.get(
        f"/api/voices/{payload['id']}", headers={"X-User-ID": "different-user"}
    )
    assert hidden.status_code == 404

    deleted = client.delete(
        f"/api/voices/{payload['id']}", headers={"X-User-ID": "voice-owner"}
    )
    assert deleted.status_code == 204
    assert client.get("/api/voices", headers={"X-User-ID": "voice-owner"}).json()["items"] == []


def test_outdated_consent_version_is_rejected(
    voice_app: tuple[TestClient, StubFileService],
) -> None:
    client, _ = voice_app
    response = client.post(
        "/api/voices",
        headers={"X-User-ID": "voice-owner"},
        data=profile_form(version="old-version"),
        files={"voice_sample": ("voice.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    )
    assert response.status_code == 422


def test_speech_input_is_scoped_to_uploader(
    voice_app: tuple[TestClient, StubFileService],
) -> None:
    client, _ = voice_app
    profile = client.post(
        "/api/voices",
        headers={"X-User-ID": "voice-owner"},
        data=profile_form(),
        files={"voice_sample": ("voice.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    ).json()
    uploaded = client.post(
        "/api/files/inputs",
        headers={"X-User-ID": "voice-owner"},
        data={"noise_reduction": "normal"},
        files={"audio_file": ("speech.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    )
    other_profile = client.post(
        "/api/voices",
        headers={"X-User-ID": "different-user"},
        data=profile_form(),
        files={"voice_sample": ("other.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    ).json()

    assert uploaded.status_code == 201
    input_key = uploaded.json()["input_storage_key"]
    accepted = client.post(
        "/api/jobs",
        headers={"X-User-ID": "voice-owner", "Idempotency-Key": "speech-input-owner"},
        json={
            "mode": "speech_voice_conversion",
            "model_key": "mock-universal-v1",
            "voice_profile_id": profile["id"],
            "input_storage_key": input_key,
        },
    )
    stolen = client.post(
        "/api/jobs",
        headers={"X-User-ID": "different-user", "Idempotency-Key": "speech-input-other"},
        json={
            "mode": "speech_voice_conversion",
            "model_key": "mock-universal-v1",
            "voice_profile_id": other_profile["id"],
            "input_storage_key": input_key,
        },
    )

    assert accepted.status_code == 202
    assert stolen.status_code == 422


def test_singing_input_preserves_music_sample_rate_and_channels(
    voice_app: tuple[TestClient, StubFileService],
) -> None:
    client, file_service = voice_app

    uploaded = client.post(
        "/api/files/inputs",
        headers={"X-User-ID": "singer"},
        data={"input_kind": "singing", "noise_reduction": "off"},
        files={"audio_file": ("song.wav", b"RIFF0000WAVEaudio", "audio/wav")},
    )

    assert uploaded.status_code == 201
    assert uploaded.json()["sample_rate"] == 44_100
    assert uploaded.json()["channels"] == 2
    assert file_service.last_config == PreprocessingConfig(
        target_sample_rate=44_100,
        target_channels=2,
        trim_silence=False,
        normalize_loudness=False,
        noise_reduction="off",
    )

