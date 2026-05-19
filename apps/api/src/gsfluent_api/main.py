"""gsfluent v2 API entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import re
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=get_settings().cors_allow_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-trace-id"],
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


# ---------- SPA serving ---------------------------------------------------
#
# When the built frontend lives at GSFLUENT_SPA_DIR (default /srv/spa),
# the api hosts it at /. This makes the frontend -> backend connection
# *same-origin* — the SPA's fetch('/v1/...') just hits this app, no
# CORS, no shell-script wrappers, no jq, no proxy.
#
# Vite's `assets/<hash>.js` paths are served directly. Any path that
# doesn't match a file falls back to index.html so client-side routing
# (TanStack Router) takes over.
#
# /v1/* and /metrics routers are registered above, so they take
# precedence over the catchall below.

_spa_dir = Path(get_settings().spa_dir)
if (_spa_dir / "index.html").is_file():
    _spa_index = _spa_dir / "index.html"

    # index.html MUST NOT be cached. Vite's hashed asset files
    # (/assets/<name>-<hash>.<ext>) are content-addressed and safe to
    # cache for a year; index.html is the manifest pointing at those
    # hashes — a stale index.html strands the browser on 404s.
    _NOCACHE = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    _IMMUTABLE = {
        "Cache-Control": "public, max-age=31536000, immutable",
    }

    # Matches Vite's hashed-asset URLs: /assets/<name>-<hash>.<ext>.
    _HASHED_ASSET_RE = re.compile(
        r"^assets/[A-Za-z0-9_.$]+-[A-Za-z0-9_-]{6,}\.[a-z0-9]+$",
    )

    # If the browser asks for a hashed JS asset we don't have, the SPA
    # they're running was loaded from a stale index.html — likely
    # because they cached the old v1 manifest (or any prior v2 build).
    # Return a tiny script that cache-busts + reloads; the next pass
    # gets the current index.html (no-cache) and the right hashes.
    _STALE_ASSET_JS = (
        "console.warn('gsfluent: stale SPA cache; reloading');\n"
        "var u = new URL(window.location.href);\n"
        "u.searchParams.set('_v', String(Date.now()));\n"
        "window.location.replace(u.href);\n"
    )

    @app.get("/", include_in_schema=False)
    async def spa_root() -> FileResponse:
        return FileResponse(_spa_index, headers=_NOCACHE)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> Response:
        # /v1/* + /metrics are handled by their routers above. Legacy
        # v1 API paths (/api/*) must 404 here, not fall back to
        # index.html.
        if full_path.startswith(("v1/", "api/", "metrics")):
            raise HTTPException(404, f"unknown path /{full_path}")

        candidate = _spa_dir / full_path
        if candidate.is_file():
            # Hashed assets are content-addressed — cache forever.
            if _HASHED_ASSET_RE.match(full_path):
                return FileResponse(candidate, headers=_IMMUTABLE)
            return FileResponse(candidate)

        # Stale-cache recovery: missing hashed JS bundle.
        if _HASHED_ASSET_RE.match(full_path) and full_path.endswith(".js"):
            return Response(
                content=_STALE_ASSET_JS,
                media_type="text/javascript",
                headers={"Cache-Control": "no-store"},
            )

        if not _spa_index.is_file():
            raise HTTPException(404, "spa index missing")
        # SPA route fallback (TanStack Router takes over client-side).
        return FileResponse(_spa_index, headers=_NOCACHE)

