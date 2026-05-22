"""Integration test: a bad recipe is rejected at the API boundary with
422, BEFORE any subprocess gets spawned.

We assert two things:
  1. The HTTP response is 422 with the envelope shape.
  2. No process matching the sim binary is running after the request.

This is the spec's correctness guarantee for the recipe-trust boundary:
the GPU only sees recipes that passed strict Pydantic + check_recipe_caps.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("GSFLUENT_REQUIRE_GPU", "0")
    monkeypatch.setenv("GSFLUENT_MAX_PARTICLE_COUNT", "500000")
    monkeypatch.setenv("GSFLUENT_MAX_WALL_TIME_SEC", "3600")
    sh = tmp_path / "sim_home"
    sh.mkdir()
    return AppConfig(
        sim_home=sh,
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(max_particle_count=500_000, max_wall_time_sec=3600),
    )


@pytest.fixture
def client(cfg: AppConfig) -> TestClient:
    return TestClient(build_app(cfg))


def _no_mpm_sim_running() -> bool:
    """Best-effort check: no `gs_simulation_building.py` subprocess on this host.

    We use `pgrep` if available; otherwise we read /proc directly.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gs_simulation_building.py"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        # pgrep returns 1 when no matches; 0 when matches found.
        return result.returncode == 1
    except (FileNotFoundError, subprocess.SubprocessError):
        # Fallback: walk /proc/*/cmdline.
        for proc_dir in Path("/proc").glob("[0-9]*"):
            try:
                cmdline = (proc_dir / "cmdline").read_bytes()
            except (FileNotFoundError, PermissionError):
                continue
            if b"gs_simulation_building.py" in cmdline:
                return False
        return True


def test_over_cap_recipe_returns_422_without_spawning(
    client: TestClient, tmp_path: Path
) -> None:
    """A particle-count-over-cap recipe is rejected; no sim subprocess fires."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    assert _no_mpm_sim_running(), "test prerequisite: no leftover sim before run"

    resp = client.post("/api/runs", json={
        "run_name": "rejected_early_test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 5_000_000},
        "recipe_source": "manual",
        "particles": 5_000_000,
    })

    assert resp.status_code == 422
    body = resp.json()
    envelope = body["detail"] if "detail" in body else body
    assert envelope["error"]["kind"] == "cap_exceeded.particle_count"

    # And no sim subprocess was started.
    assert _no_mpm_sim_running(), "sim subprocess fired despite 422 rejection"


def test_invalid_recipe_shape_returns_422_without_spawning(
    client: TestClient, tmp_path: Path
) -> None:
    """A strict-Pydantic rejection (wrong type) is also pre-spawn."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    assert _no_mpm_sim_running()

    resp = client.post("/api/runs", json={
        "run_name": "bad_shape_test",
        "model_path": str(model_dir),
        "recipe_data": "this should be an object",
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    envelope = body["detail"] if "detail" in body else body
    assert envelope["error"]["kind"].startswith("validation.")
    assert _no_mpm_sim_running()
