"""App config — env-driven Pydantic settings.

All values come from env (or .env file in dev). Failing fast on missing
required values is the goal; never silently fall back to localhost defaults.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Database / cache / storage
    database_url: str = Field(...)
    redis_url: str = Field(...)
    minio_endpoint: str = Field(...)
    minio_access_key: str = Field(...)
    minio_secret_key: str = Field(...)
    minio_secure: bool = False

    # Concurrency caps (overridable in Redis at runtime via /v1/system/config)
    max_concurrent_sims: int = 1
    max_concurrent_renders: int = 5

    # Observability
    sentry_dsn: str = ""
    log_level: str = "info"

    # Build metadata (set by Docker build / CI)
    version: str = "dev"
    git_sha: str = "unknown"

    # Where the built SPA lives. When this dir exists + has index.html,
    # the api serves it at / with SPA-style fallback routing. Same-origin
    # solves the frontend->backend connection without CORS hacks.
    spa_dir: str = "/srv/spa"

    # CORS for non-browser tooling (curl, postman, programmatic clients
    # on other hosts). The SPA doesn't need this since it's same-origin.
    # Regex permissive for internal-network demo; tighten before external.
    cors_allow_origin_regex: str = r".*"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
