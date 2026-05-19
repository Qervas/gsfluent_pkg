"""gsfluent v2 API entry point.

Phase 1 Task 1.1 — scaffold. Real endpoints land in subsequent tasks:
- 1.3-1.4: data model + Alembic
- 1.6: real /v1/system/health with PG/Redis/MinIO/GPU sub-checks
- 1.7: structlog + Prometheus middleware
- Phase 2+: models, recipes, runs, render-sessions, system config
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__

app = FastAPI(
    title="gsfluent v2",
    version=__version__,
    openapi_url="/v1/openapi.json",
    docs_url="/v1/docs",
    redoc_url=None,
    default_response_class=__import__("fastapi.responses", fromlist=["ORJSONResponse"]).ORJSONResponse,
)


@app.get("/v1/system/health")
async def health() -> dict[str, str]:
    """Placeholder health endpoint. Task 1.6 replaces this with real sub-checks."""
    return {"status": "ok", "version": __version__}
