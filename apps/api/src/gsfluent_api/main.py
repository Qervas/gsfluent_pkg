"""gsfluent v2 API entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import sentry_sdk
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from . import __version__
from .config import get_settings
from .logging_setup import configure_logging, get_logger
from .middleware import TraceIdMiddleware
from .queue import close_queue
from .routes.artifacts import router as artifacts_router
from .routes.models import router as models_router
from .routes.recipes import router as recipes_router
from .routes.render_sessions import router as render_sessions_router
from .routes.runs import router as runs_router
from .routes.stream import router as stream_router
from .routes.system import router as system_router
from .storage import ensure_buckets


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level)
    if s.sentry_dsn:
        sentry_sdk.init(dsn=s.sentry_dsn, release=s.version, environment="v2")
    # Idempotent — creates gsfluent-models / gsfluent-runs / gsfluent-misc
    # if they're missing. Removes the manual one-off step on fresh boots.
    try:
        await ensure_buckets()
    except Exception as e:  # noqa: BLE001
        get_logger().warning("ensure_buckets_failed", error=str(e)[:200])
    get_logger().info("api.start", version=__version__, git_sha=s.git_sha)
    yield
    await close_queue()
    get_logger().info("api.stop")


app = FastAPI(
    title="gsfluent v2",
    version=__version__,
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(TraceIdMiddleware)
Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

app.include_router(system_router)
app.include_router(models_router)
app.include_router(recipes_router)
app.include_router(runs_router)
app.include_router(artifacts_router)
app.include_router(render_sessions_router)
app.include_router(stream_router)
