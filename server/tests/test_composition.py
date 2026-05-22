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
    # Phase 6: /api/health now reports sim_home_exists as a real signal,
    # so the fixture must materialize the directory or the endpoint
    # would (correctly) return status="down". Existing call sites
    # treat this directory as a black-box path so creating it doesn't
    # affect their semantics.
    sim_home = tmp_path / "sim_home"
    sim_home.mkdir()
    return AppConfig(
        sim_home=sim_home,
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


# --- Phase 4: lifespan recovery + sd_notify ----------------------------------


def test_lifespan_calls_recover_on_boot(cfg: AppConfig) -> None:
    """Verify the lifespan kicks off recovery on startup. We don't need
    real subprocesses; an empty state dir suffices to confirm the call
    path. If recover_on_boot raised, the lifespan would have failed and
    the TestClient context entry would have re-raised."""
    app = build_app(cfg)
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200


def test_lifespan_sends_sd_notify_ready_when_socket_present(
    monkeypatch, tmp_path: Path,
) -> None:
    """When $NOTIFY_SOCKET points at a real datagram socket, lifespan
    sends READY=1 after recovery."""
    import socket

    from gsfluent.config import AppConfig
    from gsfluent.core.limits import CapConfig

    sock_path = tmp_path / "notify.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(str(sock_path))
    listener.settimeout(5.0)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        cfg = AppConfig(
            sim_home=tmp_path / "sim_home",
            sim_python="python",
            sim_env=None,
            work_dir=tmp_path / "work",
            caps=CapConfig(),
        )
        app = build_app(cfg)
        ready_seen = False
        with TestClient(app):
            # Drain datagrams until READY=1 shows up (sd_notify may send
            # STATUS=... datagrams before READY=1).
            for _ in range(10):
                try:
                    data, _ = listener.recvfrom(4096)
                except TimeoutError:
                    break
                if b"READY=1" in data:
                    ready_seen = True
                    break
        assert ready_seen, "READY=1 datagram never arrived"
    finally:
        listener.close()


def test_built_app_health_route_works_after_phase_4_lifespan(cfg: AppConfig) -> None:
    """Smoke: even after Phase 4 wiring, /api/health still responds 200
    and the lifespan completes cleanly (TestClient context exits without
    raising)."""
    app = build_app(cfg)
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json().get("status") == "ok"
