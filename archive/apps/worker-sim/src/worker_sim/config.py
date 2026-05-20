"""Sim worker config — same env vars as the api so docker-compose can
share the env file."""

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

    worker_id: str = "worker-sim-0"
    cancel_check_frames: int = 10  # how often to poll the cancel flag


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
