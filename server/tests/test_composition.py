"""Tests for the composition root."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        sim_home=tmp_path / "sim_home",
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(),
    )


def test_build_app_returns_fastapi_instance(cfg: AppConfig) -> None:
    app = build_app(cfg)
    assert isinstance(app, FastAPI)


def test_built_app_responds_to_health(cfg: AppConfig) -> None:
    app = build_app(cfg)
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_built_app_creates_work_dirs(cfg: AppConfig) -> None:
    """Composition root should ensure work_dir + _state/runs exists on startup."""
    build_app(cfg)
    assert (cfg.work_dir / "_state" / "runs").is_dir()


def test_create_app_delegates_to_build_app(monkeypatch, tmp_path: Path) -> None:
    """server.create_app() should call composition.build_app(AppConfig.from_env())."""
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(tmp_path))
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.setenv("GSFLUENT_WORK_DIR", str(tmp_path / "work"))

    from gsfluent.server import create_app
    app = create_app()
    assert isinstance(app, FastAPI)
    # Sanity check: the same routes the original app exposed are still there.
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200


# --- Phase 2: concrete impls attached to app.state ---------------------------


def test_built_app_has_storage_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.storage import Storage
    app = build_app(cfg)
    s = getattr(app.state, "storage", None)
    assert s is not None
    assert isinstance(s, Storage)


def test_built_app_has_cache_codec_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.cache import CacheCodec
    app = build_app(cfg)
    c = getattr(app.state, "cache_codec", None)
    assert c is not None
    assert isinstance(c, CacheCodec)


def test_built_app_has_fuser_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.fuse import Fuser
    app = build_app(cfg)
    f = getattr(app.state, "fuser", None)
    assert f is not None
    assert isinstance(f, Fuser)


def test_built_app_has_run_mgr_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.runs import RunManager
    app = build_app(cfg)
    rm = getattr(app.state, "run_mgr", None)
    assert rm is not None
    assert isinstance(rm, RunManager)


def test_built_app_has_obs_on_state(cfg: AppConfig) -> None:
    from gsfluent.protocols.observability import EventEmitter
    app = build_app(cfg)
    obs = getattr(app.state, "obs", None)
    assert obs is not None
    assert isinstance(obs, EventEmitter)
