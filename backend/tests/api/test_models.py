from fastapi.testclient import TestClient


def test_models_can_be_filtered_by_capability(client: TestClient) -> None:
    response = client.get("/api/models", params={"capability": "singing_voice_conversion"})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["key"] == "mock-universal-v1"
    assert payload["items"][0]["is_mock"] is True
