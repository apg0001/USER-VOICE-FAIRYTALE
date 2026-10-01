import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import RecordingQueue, create_test_schema
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import redact_sensitive
from app.main import create_app


@pytest.fixture
def operations_client(tmp_path: Path) -> Iterator[TestClient]:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}"
    asyncio.run(create_test_schema(database_url))
    settings = Settings(
        app_env="test",
        database_url=database_url,
        storage_path=tmp_path / "storage",
        model_path=tmp_path / "models",
        use_mock_inference=True,
    )
    with TestClient(create_app(settings, job_queue=RecordingQueue())) as client:
        yield client


def test_readiness_metrics_trace_and_security_headers(
    operations_client: TestClient,
) -> None:
    ready = operations_client.get(
        "/api/ready",
        headers={"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"},
    )
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {"database": "ok", "storage": "ok"},
    }
    assert ready.headers["X-Trace-ID"] == "0123456789abcdef0123456789abcdef"
    assert ready.headers["X-Content-Type-Options"] == "nosniff"
    assert ready.headers["Cache-Control"] == "no-store"

    metrics = operations_client.get("/api/metrics")
    assert metrics.status_code == 200
    assert (
        'voice_http_requests_total{method="GET",route="/api/ready",status="200"} 1' in metrics.text
    )


def test_trusted_proxy_authentication_and_production_guard(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="AUTH_MODE"):
        create_app(Settings(app_env="production"))

    database_url = f"sqlite+aiosqlite:///{tmp_path / 'proxy.db'}"
    asyncio.run(create_test_schema(database_url))
    proxy_secret = "gateway-secret-that-is-at-least-32-chars"
    app = create_app(
        Settings(
            app_env="test",
            auth_mode="trusted_proxy",
            trusted_proxy_secret=proxy_secret,
            database_url=database_url,
            storage_path=tmp_path / "storage",
        ),
        job_queue=RecordingQueue(),
    )
    with TestClient(app) as client:
        rejected = client.get("/api/jobs", headers={"X-User-ID": "spoofed"})
        accepted = client.get(
            "/api/jobs",
            headers={
                "X-Authenticated-User": "verified-subject",
                "X-Proxy-Secret": proxy_secret,
            },
        )
    assert rejected.status_code == 401
    assert accepted.status_code == 200


def test_rate_limit_and_sensitive_log_redaction(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'rate.db'}",
            storage_path=tmp_path / "storage",
            rate_limit_requests_per_minute=1,
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/models").status_code == 200
        limited = client.get("/api/models")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0

    redacted = redact_sensitive(
        object(),
        "info",
        {
            "event": "sample",
            "authorization": "Bearer secret",
            "nested": {"input_text": "private story", "safe": "kept"},
            "payload": b"voice bytes",
        },
    )
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"] == {"input_text": "[REDACTED]", "safe": "kept"}
    assert redacted["payload"] == "[BINARY REDACTED]"
