"""Composition root — single place where concrete impls get wired into the app.

Phase 1 wired EventEmitter and ensured work directories existed.
Phase 2 grows that: FilesystemStorage, GSQCodec, KNNKabschFuser, and
AsyncioRunManager land here, attached to app.state for downstream
Depends() retrieval (which Phase 3 will use to rewire api/runs.py and
api/sequences.py).
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gsfluent._paths import PKG_ROOT
from gsfluent.config import AppConfig
from gsfluent.core.codecs.gsq import GSQCodec
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.cache import CacheCodec
from gsfluent.protocols.fuse import Fuser
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import RunManager
from gsfluent.protocols.storage import Storage
from gsfluent.storage.filesystem import FilesystemStorage


def _ensure_work_dirs(cfg: AppConfig) -> None:
    """Create the on-disk directory layout the backend expects."""
    (cfg.work_dir / "_state" / "runs").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "library" / "sequences").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "cache" / "viser").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "uploads").mkdir(parents=True, exist_ok=True)


def _add_legacy_introspection_routes(app: FastAPI) -> None:
    """Attach the introspection routes the original server.create_app() served.

    Preserved verbatim so the existing test suite + deployment handshake
    flows (gpu-check, system info, root index) don't regress when the
    composition root takes over. Health route lives here too so the
    response shape (status + pkg_root) matches the previous contract.
    """

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "pkg_root": str(PKG_ROOT)}

    @app.get("/api/gpu-check")
    async def gpu_check() -> dict:
        """Probe the host's NVIDIA GPU(s) via nvidia-smi. Used as a
        deployment-time handshake: confirms (1) nvidia-smi is reachable
        from this process (so the container was started with `--gpus all`
        or the host has the CUDA toolkit on PATH), and (2) at least one
        GPU is visible. Returns the raw CSV rows for the caller to
        inspect."""
        smi = shutil.which("nvidia-smi")
        if smi is None:
            return {
                "ok": False,
                "error": "nvidia-smi not on PATH",
                "hint": (
                    "If running in Docker: was the container started with "
                    "`--gpus all` and is the nvidia-container-toolkit "
                    "installed on the host? On bare metal: install the "
                    "NVIDIA driver + CUDA toolkit so nvidia-smi is on PATH."
                ),
            }
        try:
            # Note: `cuda_version` was historically queryable but newer
            # drivers (565+) reject it. Stick to fields available in
            # both old and new nvidia-smi.
            out = subprocess.check_output(
                [smi,
                 "--query-gpu=index,name,driver_version,memory.total,memory.free",
                 "--format=csv,noheader"],
                text=True, timeout=5,
            ).strip()
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "nvidia-smi timed out (>5s)"}
        except subprocess.CalledProcessError as e:
            return {"ok": False, "error": f"nvidia-smi exit {e.returncode}",
                    "stderr": (e.stderr or "").strip()}
        return {
            "ok": True,
            "gpus": [line.strip() for line in out.splitlines() if line.strip()],
        }

    @app.get("/api/system")
    async def system_info() -> dict:
        """Container/host introspection. Useful before submitting the
        first sim run — confirms the backend is the version + env the
        deployer expects. No secrets exposed."""
        return {
            "hostname":      socket.gethostname(),
            "platform":      platform.platform(),
            "python":        sys.version.split()[0],
            "pkg_root":      str(PKG_ROOT),
            "sim_script":    os.environ.get("GSFLUENT_SIM_SCRIPT_RUNNER", "<default>"),
            "sim_home":      os.environ.get("GSFLUENT_SIM_HOME", "<default>"),
            "in_container":  Path("/.dockerenv").exists(),
        }

    @app.get("/")
    async def root() -> dict:
        return {
            "service": "gsfluent",
            "version": "0.1.0",
            "hint": "API-only backend. The SPA runs locally — see README.",
            "endpoints": ["/api/health", "/api/system", "/api/recipes", "/docs"],
        }


def build_app(cfg: AppConfig) -> FastAPI:
    """Construct the FastAPI app with all concrete dependencies wired.

    Phase 2 attaches the new concretes to app.state so Phase 3 can swap
    api/runs.py + api/sequences.py to Depends()-based injection. Existing
    routers continue to call `runner.start_run` / `runner.cancel_run`
    directly — that wiring is unchanged in Phase 2.
    """
    _ensure_work_dirs(cfg)

    obs: EventEmitter = StdlibJSONEmitter(stream=sys.stdout)
    obs.emit("backend.boot", work_dir=str(cfg.work_dir), sim_home=str(cfg.sim_home))

    # Concrete impls.
    storage: Storage = FilesystemStorage(root=cfg.work_dir / "cache" / "viser")
    cache_codec: CacheCodec = GSQCodec()
    fuser: Fuser = KNNKabschFuser(k=8)
    state_store = RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")

    # Phase 2 shim placeholder: MPMSimulationEngine lands in Phase 3 and
    # replaces this. The Phase 2 shim delegates to core.runner module
    # functions and never dispatches through `sim_engine`, so a placeholder
    # that raises on use is the safest fail-loud Phase 3-trigger.
    class _DeferredSimEngine:
        async def run(self, *a, **kw):
            raise NotImplementedError(
                "Phase 3 wires MPMSimulationEngine here; Phase 2's shim still "
                "delegates to core.runner module functions and must not call this."
            )

    run_mgr: RunManager = AsyncioRunManager(
        sim_engine=_DeferredSimEngine(),
        fuser=fuser,
        cache_codec=cache_codec,
        storage=storage,
        obs=obs,
        state_store=state_store,
        wall_time_cap_sec=cfg.caps.max_wall_time_sec,
        particle_count_cap=cfg.caps.max_particle_count,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Phase 4 will replace this with the real recover_on_boot wiring;
        # Phase 2 calls it now to exercise the shim path on startup.
        obs.emit("backend.lifespan.startup")
        try:
            report = await run_mgr.recover_on_boot()
            obs.emit(
                "boot.recovery_complete",
                reattached=report.reattached,
                interrupted=report.interrupted,
                terminal_already=report.terminal_already,
            )
        except Exception as e:
            obs.emit("boot.recovery_failed", error=str(e))
        yield
        obs.emit("backend.lifespan.shutdown")

    app = FastAPI(title="gsfluent", version="0.1.0", lifespan=lifespan)

    # Attach concretes to app.state so Depends() lookups work in Phase 3.
    app.state.obs = obs
    app.state.storage = storage
    app.state.cache_codec = cache_codec
    app.state.fuser = fuser
    app.state.run_mgr = run_mgr
    app.state.state_store = state_store

    # CORS — match the existing policy: allow any localhost/127.0.0.1
    # port (vite dev :5173, vite preview :4173, or any user-chosen port),
    # plus optional comma-separated extras from GSFLUENT_EXTRA_CORS_ORIGINS
    # so deploys behind a public-IP port-mapping can let the SPA hit the
    # API directly without a tunnel.
    extra = [
        o.strip()
        for o in os.environ.get("GSFLUENT_EXTRA_CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=extra,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Legacy introspection routes (health, gpu-check, system, root index).
    # These predate the Phase 1 refactor; preserved verbatim so existing
    # deployment handshakes and the test suite keep working.
    _add_legacy_introspection_routes(app)

    # Mount existing routers (unchanged in Phase 1; Phase 3+ will rewire
    # them through Depends() against the new Protocols).
    from gsfluent.api import (
        recipes as recipes_api,
        models as models_api,
        runs as runs_api,
        sequences as sequences_api,
        stream as stream_api,
        schemas as schemas_api,
    )
    app.include_router(recipes_api.router)
    app.include_router(models_api.router)
    app.include_router(runs_api.router)
    app.include_router(sequences_api.router)
    app.include_router(stream_api.router)
    app.include_router(schemas_api.router)

    return app
