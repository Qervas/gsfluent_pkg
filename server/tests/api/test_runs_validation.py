"""Tests for strict Pydantic + cap checking on POST /api/runs.

Every rejection must:
  - return HTTP 422
  - carry the {"error": {"kind", "message", "details", "trace_id"}} envelope
  - the `kind` is `validation.<field>` for Pydantic rejections and
    `cap_exceeded.<axis>` for cap violations.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(tmp_path / "sim_home"))
    monkeypatch.setenv("GSFLUENT_MAX_PARTICLE_COUNT", "500000")
    monkeypatch.setenv("GSFLUENT_MAX_WALL_TIME_SEC", "3600")
    monkeypatch.setenv("GSFLUENT_MAX_RECIPE_BYTES", str(16 * 1024))
    (tmp_path / "sim_home").mkdir()
    return AppConfig(
        sim_home=tmp_path / "sim_home",
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(
            max_particle_count=500_000,
            max_wall_time_sec=3600,
            max_recipe_bytes=16 * 1024,
        ),
    )


@pytest.fixture
def client(cfg: AppConfig) -> TestClient:
    return TestClient(build_app(cfg))


# ---------- envelope shape -----------------------------------------------


def _assert_envelope_shape(body: dict, expected_kind: str) -> None:
    assert "error" in body, f"missing 'error' key in body: {body}"
    err = body["error"]
    assert err["kind"] == expected_kind, f"expected {expected_kind}, got {err['kind']}"
    assert isinstance(err["message"], str)
    assert isinstance(err["details"], dict)
    assert isinstance(err["trace_id"], str)
    assert len(err["trace_id"]) >= 16


# ---------- Pydantic strict-mode rejection -------------------------------


def test_missing_run_name_returns_422_validation(client: TestClient) -> None:
    resp = client.post("/api/runs", json={
        "model_path": "/tmp/model",
        "recipe_data": {"particle_count": 100},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI's HTTPException wraps the envelope under detail; we accept either.
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    _assert_envelope_shape(body, expected_kind="validation.run_name")


def test_particle_count_wrong_type_returns_422_validation(client: TestClient) -> None:
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": "/tmp/model",
        "recipe_data": {"particle_count": "lots"},
        "recipe_source": "manual",
        "particles": "abc",
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    assert body["error"]["kind"].startswith("validation.")


def test_unknown_extra_field_rejected_in_strict_mode(client: TestClient) -> None:
    """Pydantic strict mode forbids unknown fields on StartRunRequest."""
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": "/tmp/model",
        "recipe_data": {},
        "recipe_source": "manual",
        "secret_admin_flag": True,
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    assert body["error"]["kind"].startswith("validation.")


def test_unsafe_run_name_rejected(client: TestClient) -> None:
    """Run names with path separators / suspicious chars are rejected."""
    resp = client.post("/api/runs", json={
        "run_name": "../../etc/passwd",
        "model_path": "/tmp/model",
        "recipe_data": {},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    assert body["error"]["kind"] == "validation.run_name"


# ---------- cap checking -------------------------------------------------


def test_particle_count_over_cap_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 1_000_000},
        "recipe_source": "manual",
        "particles": 1_000_000,
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    _assert_envelope_shape(body, expected_kind="cap_exceeded.particle_count")
    assert body["error"]["details"]["requested"] == 1_000_000
    assert body["error"]["details"]["limit"] == 500_000


def test_wall_time_over_cap_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 100, "wall_time_sec": 9999},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    _assert_envelope_shape(body, expected_kind="cap_exceeded.wall_time")


def test_wall_time_wrong_type_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 100, "wall_time_sec": "soon"},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    _assert_envelope_shape(body, expected_kind="cap_exceeded.wall_time")


def test_wall_time_non_positive_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 100, "wall_time_sec": 0},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    _assert_envelope_shape(body, expected_kind="cap_exceeded.wall_time")


def test_recipe_size_over_cap_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    huge_recipe = {"particle_count": 100, "noise": "x" * (20 * 1024)}
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": huge_recipe,
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    if "detail" in body and isinstance(body["detail"], dict) and "error" in body["detail"]:
        body = body["detail"]
    _assert_envelope_shape(body, expected_kind="cap_exceeded.recipe_size")
