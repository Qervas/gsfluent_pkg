"""Filesystem layout — single source of truth.

Anything that wants to know where the repo, the library, the splat cache,
or a per-sequence dir lives must import from here. Don't re-derive paths
with `Path(__file__).resolve().parents[N]` elsewhere; the offset changes
the moment a file moves, and it has bitten us before.
"""
from __future__ import annotations

import os
from pathlib import Path

# server/gsfluent/_paths.py -> server/gsfluent -> server -> repo root
PKG_ROOT = Path(__file__).resolve().parents[2]


def _configured_work_root() -> Path:
    raw = os.environ.get("GSFLUENT_WORK_DIR")
    return Path(raw) if raw else PKG_ROOT / "work"


WORK = _configured_work_root()
CACHE_SPLATS = WORK / "cache" / "splats"
LIBRARY = WORK / "library"
SEQUENCES = LIBRARY / "sequences"
LOGS = WORK / "logs"

SERVER_DIR = PKG_ROOT / "server"
SERVER_TOOLS = SERVER_DIR / "tools"
SERVER_RECIPES = SERVER_DIR / "recipes"


def gsq_for(name: str) -> Path:
    return CACHE_SPLATS / f"{name}.gsq"


def sequence_dir_for(name: str) -> Path:
    return SEQUENCES / name
