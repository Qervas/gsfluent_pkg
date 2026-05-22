"""Tests for the real /api/health endpoint.

The endpoint reports five derived signals plus a top-level status discriminator.
Tests cover: (1) Pydantic contract shape, (2) status derivation matrix,
(3) graceful degradation when nvidia-smi is absent, (4) RunStateStore
integration for last_successful_run_at, (5) disk_free_pct math.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gsfluent.api.health import HealthResponse, HealthStatus, build_health_router
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig
from gsfluent.core.state import RunStateRecord, RunStateStore
from gsfluent.protocols.runs import RunState


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    sim_home = tmp_path / "sim_home"
    sim_home.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "_state" / "runs").mkdir(parents=True)
    return AppConfig(
        sim_home=sim_home,
        sim_python="python",
        sim_env=None,
        work_dir=work_dir,
        caps=CapConfig(),
    )


@pytest.fixture
def state_store(cfg: AppConfig) -> RunStateStore:
    return RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")


@pytest.fixture
def app(cfg: AppConfig, state_store: RunStateStore) -> FastAPI:
    app = FastAPI()
    app.include_router(build_health_router(cfg=cfg, state_store=state_store))
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_health_response_shape_is_locked(client: TestClient) -> None:
    """Contract test: every documented field is present and correctly typed."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    # Top-level keys (exact set — no extras, no omissions)
    assert set(body.keys()) == {
        "status",
        "gpu_reachable",
        "sim_home_exists",
        "disk_free_pct",
        "last_successful_run_at",
        "active_run_count",
        "ts",
    }
    # Type assertions
    assert body["status"] in ("ok", "degraded", "down")
    assert isinstance(body["gpu_reachable"], bool)
    assert isinstance(body["sim_home_exists"], bool)
    assert isinstance(body["disk_free_pct"], (int, float))
    assert (
        body["last_successful_run_at"] is None
        or isinstance(body["last_successful_run_at"], (int, float))
    )
    assert isinstance(body["active_run_count"], int)
    assert isinstance(body["ts"], (int, float))


def test_status_ok_when_everything_healthy(client: TestClient) -> None:
    """sim_home exists + nvidia-smi mocked as present + plenty of disk = ok."""
    with patch("gsfluent.api.health._gpu_reachable", return_value=True):
        with patch("gsfluent.api.health._disk_free_pct", return_value=87.5):
            r = client.get("/api/health")
            body = r.json()
            assert body["status"] == "ok"
            assert body["gpu_reachable"] is True
            assert body["disk_free_pct"] == 87.5


def test_status_down_when_sim_home_missing(client: TestClient, cfg: AppConfig) -> None:
    """sim_home directory removed -> status=down."""
    import shutil
    shutil.rmtree(cfg.sim_home)
    r = client.get("/api/health")
    body = r.json()
    assert body["status"] == "down"
    assert body["sim_home_exists"] is False


def test_status_down_when_disk_below_5_pct(client: TestClient) -> None:
    """disk_free_pct < 5 -> down (operator alert)."""
    with patch("gsfluent.api.health._disk_free_pct", return_value=2.0):
        r = client.get("/api/health")
        body = r.json()
        assert body["status"] == "down"
        assert body["disk_free_pct"] == 2.0


def test_status_degraded_when_gpu_unreachable(client: TestClient) -> None:
    """nvidia-smi exits non-zero or absent -> degraded."""
    with patch("gsfluent.api.health._gpu_reachable", return_value=False):
        with patch("gsfluent.api.health._disk_free_pct", return_value=50.0):
            r = client.get("/api/health")
            body = r.json()
            assert body["status"] == "degraded"
            assert body["gpu_reachable"] is False


def test_status_degraded_when_last_run_older_than_24h(
    client: TestClient, state_store: RunStateStore,
) -> None:
    """Last successful run > 24h ago -> degraded (sim pipeline may be wedged)."""
    old_finished = time.time() - (25 * 3600)
    state_store.write(RunStateRecord(
        id="old-completed",
        state=RunState.COMPLETED,
        finished_at=old_finished,
    ))
    with patch("gsfluent.api.health._gpu_reachable", return_value=True):
        with patch("gsfluent.api.health._disk_free_pct", return_value=50.0):
            r = client.get("/api/health")
            body = r.json()
            assert body["status"] == "degraded"
            assert body["last_successful_run_at"] == old_finished


def test_last_successful_run_picks_max_completed(
    client: TestClient, state_store: RunStateStore,
) -> None:
    """When multiple completions exist, report the most-recent one."""
    state_store.write(RunStateRecord(id="r1", state=RunState.COMPLETED, finished_at=1000.0))
    state_store.write(RunStateRecord(id="r2", state=RunState.COMPLETED, finished_at=2000.0))
    state_store.write(RunStateRecord(id="r3", state=RunState.FAILED,    finished_at=3000.0))
    r = client.get("/api/health")
    body = r.json()
    assert body["last_successful_run_at"] == 2000.0


def test_last_successful_run_null_when_none_completed(
    client: TestClient, state_store: RunStateStore,
) -> None:
    state_store.write(RunStateRecord(id="r1", state=RunState.QUEUED))
    r = client.get("/api/health")
    body = r.json()
    assert body["last_successful_run_at"] is None


def test_active_run_count_excludes_terminal_states(
    client: TestClient, state_store: RunStateStore,
) -> None:
    state_store.write(RunStateRecord(id="r1", state=RunState.RUNNING))
    state_store.write(RunStateRecord(id="r2", state=RunState.QUEUED))
    state_store.write(RunStateRecord(id="r3", state=RunState.COMPLETED))
    state_store.write(RunStateRecord(id="r4", state=RunState.FAILED))
    r = client.get("/api/health")
    body = r.json()
    assert body["active_run_count"] == 2  # r1 RUNNING + r2 QUEUED


def test_disk_free_pct_uses_work_dir(client: TestClient, cfg: AppConfig) -> None:
    """The disk_free_pct computation must measure cfg.work_dir's filesystem."""
    r = client.get("/api/health")
    body = r.json()
    # Real shutil.disk_usage — just assert it is in plausible bounds.
    assert 0.0 <= body["disk_free_pct"] <= 100.0


def test_gpu_reachable_false_when_nvidia_smi_absent() -> None:
    """Direct test of the helper: missing binary -> False, no exception."""
    from gsfluent.api.health import _gpu_reachable
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _gpu_reachable() is False


def test_gpu_reachable_false_on_timeout() -> None:
    import subprocess
    from gsfluent.api.health import _gpu_reachable
    with patch("subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=2)):
        assert _gpu_reachable() is False


def test_health_response_pydantic_model_round_trip() -> None:
    """The HealthResponse model accepts and serializes the contract shape."""
    h = HealthResponse(
        status=HealthStatus.OK,
        gpu_reachable=True,
        sim_home_exists=True,
        disk_free_pct=42.0,
        last_successful_run_at=1700000000.0,
        active_run_count=3,
        ts=1700000123.45,
    )
    d = h.model_dump()
    assert d["status"] == "ok"
    h2 = HealthResponse(**d)
    assert h2 == h
