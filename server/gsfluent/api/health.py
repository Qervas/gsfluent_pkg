"""Health endpoint with real signals + locked-down Pydantic contract.

Replaces the trivial /api/health stub in composition.py. The response
shape is contract-stable: SPA, systemd watchdog, and external monitoring
all rely on the keys + types defined by HealthResponse.

Status derivation matrix:
    down     := sim_home missing
    degraded := disk_free_pct < 5 OR gpu_reachable False OR last_successful_run > 24h ago
    ok       := otherwise

The systemd watchdog reads this endpoint and only sends WATCHDOG=1 when
status != "down". Keep "down" limited to process-restartable failures;
operator conditions like low disk should be "degraded" so they alert
without causing a restart loop.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from enum import Enum
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from gsfluent.config import AppConfig
from gsfluent.core.state import RunStateStore
from gsfluent.protocols.runs import TERMINAL_RUN_STATES, RunState

# --- contract types ---


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthResponse(BaseModel):
    """Locked-down /api/health response shape.

    Any field rename or addition is a breaking change — bump a version
    contract and coordinate with the SPA + watchdog before shipping.
    """
    status: HealthStatus = Field(
        ..., description="Top-level health discriminator")
    gpu_reachable: bool = Field(
        ..., description="nvidia-smi -L succeeded with at least one device")
    sim_home_exists: bool = Field(
        ..., description="cfg.sim_home is a directory")
    disk_free_pct: float = Field(
        ..., ge=0.0, le=100.0,
        description="Free disk on work_dir's filesystem")
    last_successful_run_at: float | None = Field(
        None, description="POSIX ts of most-recent COMPLETED run, or null if none"
    )
    active_run_count: int = Field(
        ..., ge=0, description="Runs in non-terminal states")
    ts: float = Field(
        ..., description="POSIX ts when this response was generated")

    model_config = {"extra": "forbid"}  # Tightens the contract.


# --- signal helpers (each one is independently mockable for tests) ---

_GPU_PROBE_TIMEOUT_SEC = 2.0
_STALE_RUN_THRESHOLD_SEC = 24 * 3600
_DISK_DEGRADED_THRESHOLD_PCT = 5.0


def _gpu_reachable() -> bool:
    """True iff nvidia-smi -L exits 0 and reports at least one device.

    Returns False (not raises) on any failure: binary absent, timeout,
    permission denied, non-zero exit. Health endpoint must never crash
    just because the GPU is gone.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=_GPU_PROBE_TIMEOUT_SEC,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired,
            PermissionError, OSError):
        return False
    if result.returncode != 0:
        return False
    # nvidia-smi -L emits one line per visible device: "GPU 0: NVIDIA A100 ..."
    return any(line.startswith("GPU") for line in result.stdout.splitlines())


def _disk_free_pct(work_dir: Path) -> float:
    """Free-space percent on work_dir's filesystem. 0..100."""
    try:
        usage = shutil.disk_usage(work_dir)
    except (FileNotFoundError, OSError):
        return 0.0
    if usage.total <= 0:
        return 0.0
    return round(usage.free / usage.total * 100.0, 2)


def _last_successful_run_at(state_store: RunStateStore) -> float | None:
    """POSIX ts of the most-recently-COMPLETED run, or None."""
    best: float | None = None
    for record in state_store.scan():
        if record.state == RunState.COMPLETED and record.finished_at is not None:
            if best is None or record.finished_at > best:
                best = record.finished_at
    return best


def _active_run_count(state_store: RunStateStore) -> int:
    """Number of records currently in non-terminal states."""
    return sum(1 for r in state_store.scan() if r.state not in TERMINAL_RUN_STATES)


def _derive_status(
    *,
    sim_home_exists: bool,
    disk_free_pct: float,
    gpu_reachable: bool,
    last_successful_run_at: float | None,
    now: float,
) -> HealthStatus:
    """Derive liveness/readiness status from independently measured signals."""
    if not sim_home_exists:
        return HealthStatus.DOWN
    if disk_free_pct < _DISK_DEGRADED_THRESHOLD_PCT:
        return HealthStatus.DEGRADED
    if not gpu_reachable:
        return HealthStatus.DEGRADED
    if (last_successful_run_at is not None
            and (now - last_successful_run_at) > _STALE_RUN_THRESHOLD_SEC):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


# --- router factory ---


def build_health_router(
    *, cfg: AppConfig, state_store: RunStateStore,
) -> APIRouter:
    """Build the /api/health router with its dependencies captured in closure.

    Construct once per app at composition time; the closure binds cfg +
    state_store so the handler doesn't need to re-read env vars on every
    request.
    """
    router = APIRouter()

    @router.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        now = time.time()
        gpu = _gpu_reachable()
        sim_home_ok = cfg.sim_home.is_dir()
        free_pct = _disk_free_pct(cfg.work_dir)
        last_at = _last_successful_run_at(state_store)
        active = _active_run_count(state_store)
        status = _derive_status(
            sim_home_exists=sim_home_ok,
            disk_free_pct=free_pct,
            gpu_reachable=gpu,
            last_successful_run_at=last_at,
            now=now,
        )
        return HealthResponse(
            status=status,
            gpu_reachable=gpu,
            sim_home_exists=sim_home_ok,
            disk_free_pct=free_pct,
            last_successful_run_at=last_at,
            active_run_count=active,
            ts=now,
        )

    return router
