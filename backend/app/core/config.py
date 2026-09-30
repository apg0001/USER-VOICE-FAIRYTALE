from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Voice Fairy Tale"
    app_env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    database_url: str = "sqlite+aiosqlite:///./voice_fairy_tale.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_path: Path = Path("./storage")
    model_path: Path = Path("./models")
    cuda_device: str = "cuda:0"
    max_upload_size: int = 500 * 1024 * 1024
    model_cache_limit: int = 1
    use_mock_inference: bool = True
    temp_retention_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    return Settings()

