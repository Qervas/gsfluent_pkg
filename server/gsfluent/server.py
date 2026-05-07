from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[2]  # gsfluent_pkg/


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup hooks land here as Phase 1 grows (recipe scan, model registry, ...).
    yield
    # Shutdown hooks land here.


def create_app() -> FastAPI:
    app = FastAPI(title="gsfluent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],   # vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "pkg_root": str(PKG_ROOT)}

    from .api import recipes as recipes_api
    app.include_router(recipes_api.router)

    return app
