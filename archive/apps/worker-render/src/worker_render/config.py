"""Render worker config — env-driven."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = Field(...)
    redis_url: str = Field(...)
    minio_endpoint: str = Field(...)
    minio_access_key: str = Field(...)
    minio_secret_key: str = Field(...)
    minio_secure: bool = False

    sentry_dsn: str = ""
    log_level: str = "info"

    worker_id: str = "worker-render-0"
    # Soft cap on simultaneous peers. Real VRAM budget enforcement is
    # spec §8.2; here we just refuse new sessions past this count.
    max_concurrent_sessions: int = 5
    # Idle timeout (no camera move + no data channel msgs) before closing.
    idle_seconds: int = 30 * 60


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
