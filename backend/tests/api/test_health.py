from fastapi.testclient import TestClient


def test_health_returns_runtime_metadata(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Voice Fairy Tale",
        "version": "0.1.0",
        "environment": "test",
    }
    assert response.headers["X-Request-ID"]
