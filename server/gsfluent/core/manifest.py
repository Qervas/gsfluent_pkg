"""Per-run manifest at <run_dir>/manifest.json.

The manifest is the durable record of a run: which model + recipe + when.
It's the source of truth for History (the frontend reads back through
/api/runs/history). Recipe is co-saved as recipe_effective.json.
"""
from __future__ import annotations

import json
import platform
import socket
import time
from pathlib import Path


def write_initial(
    run_dir: Path,
    run_name: str,
    model_dir: Path,
    recipe_source: str,
    particles: int,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_name": run_name,
        "model_dir": str(model_dir),
        "recipe_source": recipe_source,
        "particles": particles,
        "started_at": time.time(),
        "status": "running",
        "host": socket.gethostname(),
        "platform": platform.platform(),
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path


def update(run_dir: Path, **fields) -> None:
    """Merge `fields` into the existing manifest. Atomic via tmp + replace."""
    p = run_dir / "manifest.json"
    if not p.exists():
        return
    try:
        manifest = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return
    manifest.update(fields)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(manifest, indent=2))
        tmp.replace(p)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_recipe(run_dir: Path, recipe_data: dict) -> Path:
    p = run_dir / "recipe_effective.json"
    p.write_text(json.dumps(recipe_data, indent=2))
    return p
