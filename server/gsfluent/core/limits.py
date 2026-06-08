"""Recipe cap-checker. Validates a recipe dict against configured caps.

Configuration lives in CapConfig, loadable from env vars (defaults documented
on the dataclass fields). The check function raises CapExceededError on
violation — the API layer translates to HTTP 422.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from gsfluent.protocols.runs import CapExceededError

DEFAULT_MAX_PARTICLE_COUNT = 500_000
DEFAULT_MAX_WALL_TIME_SEC = 3600  # 1 hour
DEFAULT_MAX_RECIPE_BYTES = 16 * 1024  # 16 KiB


@dataclass(frozen=True)
class CapConfig:
    """Caps applied to incoming recipes.

    All caps are upper bounds — recipe requests <= these are accepted as-is.
    The wall-time cap also doubles as the orchestrator's enforcement bound
    (sim that exceeds gets PG-killed).
    """

    max_particle_count: int = DEFAULT_MAX_PARTICLE_COUNT
    max_wall_time_sec: int = DEFAULT_MAX_WALL_TIME_SEC
    max_recipe_bytes: int = DEFAULT_MAX_RECIPE_BYTES

    @classmethod
    def from_env(cls) -> CapConfig:
        def _env_int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError as e:
                raise ValueError(f"{name} must be an integer; got {raw!r}") from e

        return cls(
            max_particle_count=_env_int(
                "GSFLUENT_MAX_PARTICLE_COUNT", DEFAULT_MAX_PARTICLE_COUNT
            ),
            max_wall_time_sec=_env_int(
                "GSFLUENT_MAX_WALL_TIME_SEC", DEFAULT_MAX_WALL_TIME_SEC
            ),
            max_recipe_bytes=_env_int(
                "GSFLUENT_MAX_RECIPE_BYTES", DEFAULT_MAX_RECIPE_BYTES
            ),
        )


def check_recipe_caps(recipe: dict[str, Any], cfg: CapConfig) -> None:
    """Validate recipe against caps. Raises CapExceededError on first violation."""
    def _recipe_int(field: str, default: int) -> int:
        raw = recipe.get(field, default)
        try:
            value = int(raw)
        except (TypeError, ValueError) as e:
            label = "Wall-time hint" if field == "wall_time_sec" else field
            raise CapExceededError(f"{label} must be an integer; got {raw!r}") from e
        return value

    particle_count = _recipe_int("particle_count", 0)
    if particle_count < 0:
        raise CapExceededError(f"Particle count must be >= 0; got {particle_count}")
    if particle_count > cfg.max_particle_count:
        raise CapExceededError(
            f"Particle count {particle_count} exceeds limit {cfg.max_particle_count} "
            f"(set GSFLUENT_MAX_PARTICLE_COUNT to raise)"
        )

    # Missing wall_time_sec means "use the backend max", not "unbounded".
    wall_time_sec = _recipe_int("wall_time_sec", cfg.max_wall_time_sec)
    if wall_time_sec <= 0:
        raise CapExceededError(f"Wall-time hint must be > 0; got {wall_time_sec}")
    if wall_time_sec > cfg.max_wall_time_sec:
        raise CapExceededError(
            f"Wall-time hint {wall_time_sec}s exceeds backend max {cfg.max_wall_time_sec}s "
            f"(set GSFLUENT_MAX_WALL_TIME_SEC to raise)"
        )

    recipe_bytes = len(json.dumps(recipe).encode("utf-8"))
    if recipe_bytes > cfg.max_recipe_bytes:
        raise CapExceededError(
            f"Recipe size {recipe_bytes} bytes exceeds limit {cfg.max_recipe_bytes} bytes "
            f"(set GSFLUENT_MAX_RECIPE_BYTES to raise)"
        )
