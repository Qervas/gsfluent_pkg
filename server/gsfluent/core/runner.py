"""Subprocess wrapper around tools/sim_one.sh.

One Run = one subprocess. The runner tracks live runs in an in-process
registry so the WebSocket layer (Task 1.6) can subscribe to status events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from asyncio.subprocess import PIPE, STDOUT
from asyncio.subprocess import create_subprocess_exec as _spawn  # alias for grep-safety
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..server import PKG_ROOT
from . import manifest as manifest_mod

_log = logging.getLogger(__name__)

SIM_ONE_SH = PKG_ROOT / "tools" / "sim_one.sh"
FUSED_DIR = PKG_ROOT / "work" / "fused"


@dataclass
class Run:
    id: str
    name: str
    proc: Optional[asyncio.subprocess.Process] = None
    state: str = "queued"
    log_lines: list[str] = field(default_factory=list)


_RUNS: dict[str, Run] = {}


def get_run(run_id: str) -> Run | None:
    return _RUNS.get(run_id)


def list_runs() -> list[Run]:
    return list(_RUNS.values())


async def start_run(
    *,
    run_name: str,
    model_dir: Path,
    recipe_data: dict,
    recipe_source_name: str,
    particles: int,
) -> str:
    run_id = uuid.uuid4().hex[:12]
    run_dir = FUSED_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_mod.write_initial(run_dir, run_name, model_dir, recipe_source_name, particles)
    manifest_mod.write_recipe(run_dir, recipe_data)
    # Write the merged effective recipe to a temp file sim_one.sh can consume.
    recipe_path = run_dir / "_effective_recipe.json"
    recipe_path.write_text(json.dumps(recipe_data, indent=2))

    cmd = [
        str(SIM_ONE_SH),
        str(model_dir),
        "--config", str(recipe_path),
        "--particles", str(particles),
        "--output", run_name,
        "--live",
        "--no-vkgs-launch",
    ]
    proc = await _spawn(*cmd, stdout=PIPE, stderr=STDOUT, cwd=str(PKG_ROOT))
    run = Run(id=run_id, name=run_name, proc=proc, state="running")
    _RUNS[run_id] = run
    asyncio.create_task(_drain(run, run_dir))
    return run_id


async def _drain(run: Run, run_dir: Path) -> None:
    assert run.proc is not None and run.proc.stdout is not None
    async for raw in run.proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            run.log_lines.append(line)
            if len(run.log_lines) > 2000:
                run.log_lines = run.log_lines[-2000:]
    rc = await run.proc.wait()
    if run.state == "running":
        run.state = "done" if rc == 0 else "error"
    manifest_mod.update(
        run_dir,
        status=run.state,
        exit_code=rc,
        finished_at=time.time(),
    )


async def wait_for_run(run_id: str) -> None:
    """Block until the underlying subprocess exits. Used by tests."""
    run = _RUNS.get(run_id)
    if run is None or run.proc is None:
        return
    await run.proc.wait()
    # Yield once more so the _drain task can finish writing the manifest.
    await asyncio.sleep(0.01)


def cancel_run(run_id: str) -> bool:
    run = _RUNS.get(run_id)
    if run is None or run.proc is None or run.state != "running":
        return False
    run.proc.terminate()
    run.state = "cancelled"
    return True
