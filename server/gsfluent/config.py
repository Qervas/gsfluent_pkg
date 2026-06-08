"""AppConfig — single source of truth for backend configuration.

All env-var reads happen here. Subsystems receive a frozen AppConfig
instance (or a sub-dataclass like CapConfig) by constructor injection;
they never read os.environ directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gsfluent._paths import WORK
from gsfluent.core.limits import CapConfig


@dataclass(frozen=True)
class AppConfig:
    """Frozen backend configuration. Construct via AppConfig.from_env()."""

    # Sim wiring
    sim_home: Path
    sim_python: str
    sim_env: str | None  # optional conda env name; None = trust calling env

    # Filesystem layout
    work_dir: Path

    # Caps
    caps: CapConfig

    @classmethod
    def from_env(cls) -> AppConfig:
        sim_home_str = os.environ.get("GSFLUENT_SIM_HOME", "")
        sim_python = os.environ.get("GSFLUENT_SIM_PYTHON", "python")
        sim_env = os.environ.get("GSFLUENT_SIM_ENV") or None
        work_dir_str = os.environ.get("GSFLUENT_WORK_DIR", str(WORK))

        return cls(
            sim_home=Path(sim_home_str),
            sim_python=sim_python,
            sim_env=sim_env,
            work_dir=Path(work_dir_str),
            caps=CapConfig.from_env(),
        )
