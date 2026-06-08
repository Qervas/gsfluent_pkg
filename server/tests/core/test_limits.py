"""Tests for recipe cap-checker."""
import pytest

from gsfluent.core.limits import (
    CapConfig,
    check_recipe_caps,
)
from gsfluent.protocols.runs import CapExceededError


def test_default_caps_accept_modest_recipe() -> None:
    cfg = CapConfig()
    recipe = {"particle_count": 200_000, "wall_time_sec": 600}
    # Should not raise.
    check_recipe_caps(recipe, cfg)


def test_particle_count_cap_rejects_too_many() -> None:
    cfg = CapConfig(max_particle_count=500_000)
    recipe = {"particle_count": 800_000, "wall_time_sec": 600}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    msg = str(ei.value)
    assert "particle" in msg.lower()
    assert "800000" in msg
    assert "500000" in msg


def test_wall_time_cap_rejects_too_long() -> None:
    cfg = CapConfig(max_wall_time_sec=3600)
    recipe = {"particle_count": 100_000, "wall_time_sec": 7200}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    assert "wall" in str(ei.value).lower()


def test_wall_time_rejects_non_integer() -> None:
    cfg = CapConfig(max_wall_time_sec=3600)
    recipe = {"particle_count": 100_000, "wall_time_sec": "soon"}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    assert "wall" in str(ei.value).lower()
    assert "integer" in str(ei.value).lower()


def test_wall_time_rejects_non_positive() -> None:
    cfg = CapConfig(max_wall_time_sec=3600)
    recipe = {"particle_count": 100_000, "wall_time_sec": 0}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    assert "wall" in str(ei.value).lower()
    assert "> 0" in str(ei.value)


def test_recipe_size_cap_rejects_huge() -> None:
    cfg = CapConfig(max_recipe_bytes=1024)
    recipe = {"particle_count": 100, "wall_time_sec": 60, "noise": "x" * 5000}
    with pytest.raises(CapExceededError) as ei:
        check_recipe_caps(recipe, cfg)
    assert "size" in str(ei.value).lower() or "bytes" in str(ei.value).lower()


def test_recipe_without_particle_count_uses_default_zero() -> None:
    """Recipes missing fields should not crash the checker."""
    cfg = CapConfig()
    # No particle_count field — treat as 0, which is under any cap.
    check_recipe_caps({"wall_time_sec": 60}, cfg)


def test_recipe_without_wall_time_uses_cap_as_default() -> None:
    """Missing wall_time_sec means 'use the backend max'."""
    cfg = CapConfig(max_wall_time_sec=3600)
    # Should not raise; treated as 3600.
    check_recipe_caps({"particle_count": 100}, cfg)


def test_cap_config_from_env_uses_defaults_when_unset(monkeypatch) -> None:
    for k in ("GSFLUENT_MAX_PARTICLE_COUNT", "GSFLUENT_MAX_WALL_TIME_SEC",
              "GSFLUENT_MAX_RECIPE_BYTES"):
        monkeypatch.delenv(k, raising=False)
    cfg = CapConfig.from_env()
    assert cfg.max_particle_count > 0
    assert cfg.max_wall_time_sec > 0
    assert cfg.max_recipe_bytes > 0


def test_cap_config_from_env_reads_overrides(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_MAX_PARTICLE_COUNT", "1000000")
    monkeypatch.setenv("GSFLUENT_MAX_WALL_TIME_SEC", "7200")
    monkeypatch.setenv("GSFLUENT_MAX_RECIPE_BYTES", "65536")
    cfg = CapConfig.from_env()
    assert cfg.max_particle_count == 1_000_000
    assert cfg.max_wall_time_sec == 7200
    assert cfg.max_recipe_bytes == 65536


def test_cap_config_from_env_rejects_invalid_int(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_MAX_WALL_TIME_SEC", "soon")
    with pytest.raises(ValueError, match="GSFLUENT_MAX_WALL_TIME_SEC"):
        CapConfig.from_env()
