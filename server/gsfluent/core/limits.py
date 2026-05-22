"""Recipe cap-checker. Validates a recipe dict against configured caps.

Configuration lives in CapConfig, loadable from env vars (defaults documented
on the dataclass fields). The check function raises CapExceededError on
violation — the API layer translates to HTTP 422.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

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
    def from_env(cls) -> "CapConfig":
        return cls(
            max_particle_count=int(
                os.environ.get("GSFLUENT_MAX_PARTICLE_COUNT", DEFAULT_MAX_PARTICLE_COUNT)
            ),
            max_wall_time_sec=int(
                os.environ.get("GSFLUENT_MAX_WALL_TIME_SEC", DEFAULT_MAX_WALL_TIME_SEC)
            ),
            max_recipe_bytes=int(
                os.environ.get("GSFLUENT_MAX_RECIPE_BYTES", DEFAULT_MAX_RECIPE_BYTES)
            ),
        )


def check_recipe_caps(recipe: dict, cfg: CapConfig) -> None:
    """Validate recipe against caps. Raises CapExceededError on first violation."""
    particle_count = int(recipe.get("particle_count", 0))
    if particle_count > cfg.max_particle_count:
        raise CapExceededError(
            f"Particle count {particle_count} exceeds limit {cfg.max_particle_count} "
            f"(set GSFLUENT_MAX_PARTICLE_COUNT to raise)"
        )

    # Missing wall_time_sec means "use the backend max", not "unbounded".
    wall_time_sec = int(recipe.get("wall_time_sec", cfg.max_wall_time_sec))
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
