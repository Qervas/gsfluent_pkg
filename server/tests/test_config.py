"""Tests for AppConfig — single source of truth for backend config."""
from pathlib import Path

import pytest

from gsfluent.config import AppConfig


def test_from_env_with_required_vars_set(monkeypatch, tmp_path: Path) -> None:
    sim_home = tmp_path / "sim_home"
    sim_home.mkdir()
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(sim_home))
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("GSFLUENT_WORK_DIR", str(tmp_path / "work"))

    cfg = AppConfig.from_env()
    assert cfg.sim_home == sim_home
    assert cfg.sim_python == "/usr/bin/python3"
    assert cfg.work_dir == tmp_path / "work"
    assert cfg.sim_env is None  # optional


def test_from_env_with_optional_conda_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(tmp_path))
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.setenv("GSFLUENT_SIM_ENV", "physics")
    cfg = AppConfig.from_env()
    assert cfg.sim_env == "physics"


def test_work_dir_defaults_when_unset(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", "/tmp")
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.delenv("GSFLUENT_WORK_DIR", raising=False)
    cfg = AppConfig.from_env()
    # Default points at the repo's work/ directory (PKG_ROOT/work).
    assert cfg.work_dir.name == "work"


def test_cap_config_is_loaded(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", "/tmp")
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    monkeypatch.setenv("GSFLUENT_MAX_PARTICLE_COUNT", "750000")
    cfg = AppConfig.from_env()
    assert cfg.caps.max_particle_count == 750_000


def test_app_config_is_immutable(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", "/tmp")
    monkeypatch.setenv("GSFLUENT_SIM_PYTHON", "python")
    cfg = AppConfig.from_env()
    with pytest.raises((AttributeError, TypeError)):
        cfg.sim_python = "different"  # type: ignore[misc]
