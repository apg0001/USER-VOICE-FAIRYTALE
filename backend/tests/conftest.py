from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        storage_path=tmp_path / "storage",
        model_path=tmp_path / "models",
        use_mock_inference=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client

