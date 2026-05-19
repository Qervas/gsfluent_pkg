"""Pytest fixtures — real Postgres + Redis via testcontainers.

Per project preference: no mocks for DB / Redis. The api code runs against
real infra so we catch SQL bugs, migration drift, async issues, etc.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Spin a postgres:16 container for the test session. Returns asyncpg URL."""
    with PostgresContainer("postgres:16") as pg:
        sync_url = pg.get_connection_url()
        # testcontainers gives `postgresql+psycopg2://...`; we want asyncpg too.
        async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")

        # Install extensions our schema relies on.
        sync_engine = create_engine(sync_url)
        with sync_engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS pgcrypto'))
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))

        # Create schema from ORM metadata. Alembic is exercised by the
        # docker-compose stack on first boot; tests bypass it for speed.
        from gsfluent_api.models import Base
        Base.metadata.create_all(sync_engine)
        sync_engine.dispose()

        os.environ["DATABASE_URL"] = async_url
        yield async_url


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7-alpine") as r:
        host = r.get_container_host_ip()
        port = r.get_exposed_port(6379)
        url = f"redis://{host}:{port}/0"
        os.environ["REDIS_URL"] = url
        yield url


@pytest.fixture(scope="session", autouse=True)
def stub_minio_gpu_env(postgres_url: str, redis_url: str) -> Iterator[None]:
    """Minimal env so Settings() validates. Real MinIO is exercised in Phase 2."""
    os.environ.setdefault("MINIO_ENDPOINT", "minio:9000")
    os.environ.setdefault("MINIO_ACCESS_KEY", "test")
    os.environ.setdefault("MINIO_SECRET_KEY", "test")
    yield


@pytest_asyncio.fixture
async def db_session(postgres_url: str) -> AsyncIterator[AsyncSession]:
    """Yields a session bound to a transaction that's rolled back after the test."""
    engine = create_async_engine(postgres_url)
    async with engine.connect() as conn:
        trans = await conn.begin()
        factory = async_sessionmaker(bind=conn, expire_on_commit=False)
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(postgres_url: str, redis_url: str) -> AsyncIterator[AsyncClient]:
    """ASGI test client. Reset settings cache so env changes take effect."""
    from gsfluent_api.config import get_settings
    from gsfluent_api.db import reset_for_tests
    from gsfluent_api.main import app

    get_settings.cache_clear()
    await reset_for_tests()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
