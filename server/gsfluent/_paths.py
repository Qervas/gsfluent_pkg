"""Filesystem layout — single source of truth.

Anything that wants to know where the repo, the library, the viser cache,
or a per-sequence dir lives must import from here. Don't re-derive paths
with `Path(__file__).resolve().parents[N]` elsewhere; the offset changes
the moment a file moves, and it has bitten us before.
"""
from __future__ import annotations

from pathlib import Path

# server/gsfluent/_paths.py -> server/gsfluent -> server -> repo root
PKG_ROOT = Path(__file__).resolve().parents[2]

WORK = PKG_ROOT / "work"
CACHE_VISER = WORK / "cache" / "viser"
LIBRARY = WORK / "library"
SEQUENCES = LIBRARY / "sequences"
LOGS = WORK / "logs"

SERVER_DIR = PKG_ROOT / "server"
SERVER_TOOLS = SERVER_DIR / "tools"
SERVER_RECIPES = SERVER_DIR / "recipes"


def gsq_for(name: str) -> Path:
    return CACHE_VISER / f"{name}.gsq"


def sequence_dir_for(name: str) -> Path:
    return SEQUENCES / name


def frames_dir_for(name: str) -> Path:
    return SEQUENCES / name / "frames"


def run_log_for(name: str) -> Path:
    return SEQUENCES / name / "run.log"
