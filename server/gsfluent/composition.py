"""Composition root — single place where concrete impls get wired into the app.

Phase 1 wired EventEmitter and ensured work directories existed.
Phase 2 grows that: FilesystemStorage, GSQCodec, KNNKabschFuser, and
AsyncioRunManager land here, attached to app.state for downstream
Depends() retrieval (which Phase 3 will use to rewire api/runs.py and
api/sequences.py).

Phase 4 extends the lifespan: crash recovery runs before yielding,
sd_notify("READY=1") fires once recovery completes, and a background
watchdog task pings systemd every 15 seconds. None of this requires
systemd to be present — sd_notify helpers no-op when $NOTIFY_SOCKET
is unset.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gsfluent.config import AppConfig
from gsfluent.core.codecs.gsq import GSQCodec
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.sdnotify import notify_ready, notify_status, notify_watchdog
from gsfluent.core.sim_engines.mpm import MPMSimulationEngine
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.cache import CacheCodec
from gsfluent.protocols.fuse import Fuser
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import RunManager
from gsfluent.protocols.storage import Storage
from gsfluent.storage.filesystem import FilesystemStorage

# Watchdog heartbeat interval. systemd's WatchdogSec=30s leaves a 2x
# safety margin: if any single heartbeat misses, the next one still
# fires before systemd kills the process.
WATCHDOG_INTERVAL_SEC = 15.0


def _should_send_watchdog(status) -> bool:
    """True iff WATCHDOG=1 should be sent for the given health status.

    Per spec Section 3 Flow C: status='down' suppresses the heartbeat so
    the systemd watchdog timer fires and the unit is restarted. status
    'ok' and 'degraded' both send — degraded is "alive but worried", not
    "needs restart".
    """
    # Imported here to avoid a circular import at module load.
    from gsfluent.api.health import HealthStatus
    return status != HealthStatus.DOWN


def _current_health_status(*, cfg: AppConfig, state_store):
    """In-process health probe: builds the same status discriminator the
    /api/health endpoint reports, without going through HTTP."""
    import time as _time

    from gsfluent.api.health import (
        _derive_status,
        _disk_free_pct,
        _gpu_reachable,
        _last_successful_run_at,
    )
    return _derive_status(
        sim_home_exists=cfg.sim_home.is_dir(),
        disk_free_pct=_disk_free_pct(cfg.work_dir),
        gpu_reachable=_gpu_reachable(),
        last_successful_run_at=_last_successful_run_at(state_store),
        now=_time.time(),
    )


async def _watchdog_loop(obs: EventEmitter, health_probe=None) -> None:
    """Send WATCHDOG=1 every WATCHDOG_INTERVAL_SEC seconds, gated on health.

    Cancelled cleanly by the lifespan on shutdown. Logs a single event
    per heartbeat (one line per 15s — cheap).

    health_probe: callable returning a HealthStatus. When provided, the
    loop only sends WATCHDOG=1 when _should_send_watchdog(status) is
    True. When omitted, falls back to unconditional heartbeats (legacy
    behavior, preserved for tests that don't care about gating).
    """
    try:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
            if health_probe is not None:
                status = health_probe()
                if not _should_send_watchdog(status):
                    obs.emit(
                        "backend.watchdog.suppressed",
                        reason=str(status.value if hasattr(status, "value") else status),
                    )
                    continue
            sent = notify_watchdog()
            if sent:
                obs.emit("backend.watchdog.ping")
    except asyncio.CancelledError:
        obs.emit("backend.watchdog.stopped")
        raise


def _ensure_work_dirs(cfg: AppConfig) -> None:
    """Create the on-disk directory layout the backend expects."""
    (cfg.work_dir / "_state" / "runs").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "library" / "sequences").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "cache" / "splats").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "uploads").mkdir(parents=True, exist_ok=True)


def _add_legacy_introspection_routes(app: FastAPI) -> None:
    """Attach the root index route the original server.create_app() served.

    Preserved so the existing test suite + deployment handshake flows
    (root index) don't regress when the composition root takes over.
    Health route now lives in gsfluent.api.health and is mounted
    separately in build_app() — kept out of this helper because it needs
    the AppConfig + RunStateStore captured at composition time.
    """

    @app.get("/")
    async def root() -> dict:
        return {
            "service": "gsfluent",
            "version": "0.1.0",
            "hint": "API-only backend. The SPA runs locally — see README.",
            "endpoints": ["/api/health", "/api/recipes", "/docs"],
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
    storage: Storage = FilesystemStorage(root=cfg.work_dir / "cache" / "splats")
    cache_codec: CacheCodec = GSQCodec()
    fuser: Fuser = KNNKabschFuser(k=8)
    state_store = RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")

    # Phase 3: real MPMSimulationEngine (the deferred placeholder is gone).
    # Honor GSFLUENT_REQUIRE_GPU (default "1") so CI / tests on CPU-only hosts
    # can drop preflight without rebuilding the composition root.
    sim_engine = MPMSimulationEngine(
        sim_home=cfg.sim_home,
        sim_python=cfg.sim_python,
        sim_env=cfg.sim_env,
        require_gpu=os.environ.get("GSFLUENT_REQUIRE_GPU", "1") == "1",
        sim_fast=os.environ.get("GSFLUENT_SIM_FAST", "0") == "1",
    )

    run_mgr: RunManager = AsyncioRunManager(
        sim_engine=sim_engine,
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
        # Phase 4: recover_on_boot before yield, sd_notify READY=1, start
        # the watchdog heartbeat task. Crash recovery must not crash the
        # backend itself — recovery failure is logged but the unit still
        # comes up so the operator can investigate via /api/health and
        # journalctl.
        obs.emit("backend.lifespan.startup")
        notify_status("recovering in-flight runs")

        report = None
        try:
            report = await run_mgr.recover_on_boot()
            # AsyncioRunManager.recover_on_boot already emits
            # boot.recovery_complete; do not double-log here.
        except Exception as e:
            obs.emit("backend.recovery.failed", error=str(e))

        if report is not None:
            notify_status(
                f"ready (reattached={report.reattached} "
                f"interrupted={report.interrupted} "
                f"terminal_already={report.terminal_already})"
            )
        else:
            notify_status("ready (recovery failed; check logs)")

        notify_ready()
        obs.emit("backend.ready")

        # Phase 6: gate the watchdog heartbeat on health.status. When
        # the in-process health snapshot reports "down" (sim_home gone,
        # disk near full), suppress WATCHDOG=1 so the systemd watchdog
        # timer fires and the unit is restarted by the system.
        def _probe():
            return _current_health_status(cfg=cfg, state_store=state_store)
        watchdog_task = asyncio.create_task(
            _watchdog_loop(obs, health_probe=_probe)
        )

        try:
            yield
        finally:
            obs.emit("backend.lifespan.shutdown")
            notify_status("shutting down")
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

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

    # Legacy introspection route (root index). Predates the Phase 1
    # refactor; preserved so existing deployment handshakes and the test
    # suite keep working. (Health lives in gsfluent.api.health.)
    _add_legacy_introspection_routes(app)

    # Mount existing routers (unchanged in Phase 1; Phase 3+ will rewire
    # them through Depends() against the new Protocols).
    from gsfluent.api import (
        compose as compose_api,
    )
    from gsfluent.api import (
        models as models_api,
    )
    from gsfluent.api import (
        recipes as recipes_api,
    )
    from gsfluent.api import (
        runs as runs_api,
    )
    from gsfluent.api import (
        schemas as schemas_api,
    )
    from gsfluent.api import (
        sequences as sequences_api,
    )
    from gsfluent.api import (
        stream as stream_api,
    )
    app.include_router(recipes_api.router)
    app.include_router(compose_api.router)
    app.include_router(models_api.router)
    app.include_router(runs_api.router)
    app.include_router(sequences_api.router)
    app.include_router(stream_api.router)
    app.include_router(schemas_api.router)

    # Phase 6: real /api/health (replaces the trivial stub) with the
    # composition-root state_store + cfg captured in closure.
    from gsfluent.api.health import build_health_router
    app.include_router(build_health_router(cfg=cfg, state_store=state_store))

    return app
