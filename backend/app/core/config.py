from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    auth_mode: Literal["development_header", "trusted_proxy"] = "development_header"
    trusted_proxy_secret: SecretStr | None = None
    rate_limit_requests_per_minute: int = Field(default=120, ge=1)
    readiness_require_redis: bool = False

    database_url: str = "sqlite+aiosqlite:///./voice_fairy_tale.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_path: Path = Path("./storage")
    model_path: Path = Path("./models")
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    cuda_device: str = "cuda:0"
    max_upload_size: int = 500 * 1024 * 1024
    max_audio_duration_seconds: int = 3600
    voice_consent_version: str = "2026-09-01"
    min_voice_profile_speech_seconds: float = 10.0
    model_cache_limit: int = Field(default=2, ge=1)
    gpu_vram_reserve_mb: int = Field(default=512, ge=0)
    sse_poll_interval_seconds: float = Field(default=1.0, gt=0)
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0)
    use_mock_inference: bool = True
    temp_retention_hours: int = 24
    input_retention_hours: int = Field(default=24, ge=1)
    output_retention_hours: int = Field(default=168, ge=1)
    cleanup_batch_size: int = Field(default=100, ge=1, le=1000)
    orphan_grace_hours: int = Field(default=2, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()

