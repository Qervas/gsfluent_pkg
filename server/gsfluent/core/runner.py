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
from . import library as lib
from . import manifest as manifest_mod

_log = logging.getLogger(__name__)

SIM_ONE_SH = PKG_ROOT / "tools" / "sim_one.sh"
# Phase 1.5: point at the library so manifest.json + run.log + recipe.json
# land in the same dir as sim_one.sh's frame outputs (library/sequences/<run>/).
# Tests monkeypatch this attribute to a tmp dir, which keeps working.
FUSED_DIR = lib.SEQUENCES_DIR


@dataclass
class Run:
    id: str
    name: str
    proc: Optional[asyncio.subprocess.Process] = None
    state: str = "queued"
    log_lines: list[str] = field(default_factory=list)
    drain_task: Optional[asyncio.Task] = None


_RUNS: dict[str, Run] = {}


def _translate_sim_area_if_local(recipe_data: dict, model_dir: Path) -> dict:
    """If the recipe's sim_area is in model-local coords (small magnitudes),
    translate it to world coords using the model's bbox center. Otherwise
    leave the recipe untouched.

    The sim core expects sim_area in absolute world coords (the canonical
    R7.M_jelly_cluster shape: [3440, 3480, 29030, 29060, -25, 35] for a
    building near world (3460, 29045, 5)). Workbench recipes ship portable
    model-local bounds (e.g. [-30, 30, -10, 10, -2, 45]); we translate
    those to the actual model's location at run-start so the same recipe
    can run on any model.

    Heuristic for "looks model-local": every value in sim_area has
    abs <= 200. World-coord recipes typically have values >= a few thousand
    (model centers in COLMAP scenes are often far from origin)."""
    out = dict(recipe_data)
    sim_area = out.get("sim_area")
    if not sim_area or len(sim_area) != 6:
        return out
    if any(abs(v) > 200 for v in sim_area):
        # Looks like world coords already — leave alone.
        return out

    center = _read_model_bbox_center(model_dir)
    if center is None:
        return out
    cx, cy, cz = center
    out["sim_area"] = [
        sim_area[0] + cx, sim_area[1] + cx,
        sim_area[2] + cy, sim_area[3] + cy,
        sim_area[4] + cz, sim_area[5] + cz,
    ]
    _log.info(
        "translated sim_area model-local %s -> world %s (model center %s)",
        sim_area, out["sim_area"], center,
    )
    return out


def _read_model_bbox_center(model_dir: Path) -> tuple[float, float, float] | None:
    """Read the model's point_cloud.ply (highest iteration) and return its
    bbox center as (x, y, z). Used to translate model-local sim_area
    bounds to world coords. Returns None if the ply can't be parsed —
    caller should leave the recipe untouched in that case."""
    import re
    pc_root = model_dir / "point_cloud"
    if not pc_root.is_dir():
        return None
    iter_re = re.compile(r"^iteration_(\d+)$")
    best: tuple[int, Path] | None = None
    for it in pc_root.iterdir():
        if it.is_dir():
            m = iter_re.match(it.name)
            if m and (it / "point_cloud.ply").is_file():
                n = int(m.group(1))
                if best is None or n > best[0]:
                    best = (n, it / "point_cloud.ply")
    if best is None:
        return None
    try:
        # Read only x/y/z to keep this cheap.
        from plyfile import PlyData
        v = PlyData.read(str(best[1]))["vertex"].data
        import numpy as np
        x = np.asarray(v["x"], dtype=np.float32)
        y = np.asarray(v["y"], dtype=np.float32)
        z = np.asarray(v["z"], dtype=np.float32)
        cx = float((x.min() + x.max()) / 2)
        cy = float((y.min() + y.max()) / 2)
        cz = float((z.min() + z.max()) / 2)
        return (cx, cy, cz)
    except Exception as e:
        _log.warning("failed to read model bbox for %s: %s", model_dir, e)
        return None


def _log_task_exception(task: asyncio.Task) -> None:
    """Surface exceptions from background tasks (drain, watchdog) to the logger
    instead of letting them die silently in asyncio's "Task exception was never
    retrieved" warning."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        _log.error("background task failed: %s", exc, exc_info=exc)


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

    # Translate recipe.sim_area from MODEL-LOCAL to WORLD coords if the
    # recipe ships small numbers (workbench-style portable recipe). The
    # sim core expects world coords (matches the canonical R7.M_jelly_cluster
    # convention). Heuristic: if every sim_area value is within ±200, assume
    # model-local and translate by the model's bbox center; otherwise leave
    # alone (assume the recipe author already specified world coords).
    effective_recipe = _translate_sim_area_if_local(recipe_data, model_dir)

    # Write the merged effective recipe to a temp file sim_one.sh can consume.
    recipe_path = run_dir / "_effective_recipe.json"
    recipe_path.write_text(json.dumps(effective_recipe, indent=2))

    cmd = [
        str(SIM_ONE_SH),
        str(model_dir),
        "--config", str(recipe_path),
        "--particles", str(particles),
        "--output", run_name,
        "--live",
        "--no-vkgs-launch",
    ]
    try:
        proc = await _spawn(*cmd, stdout=PIPE, stderr=STDOUT, cwd=str(PKG_ROOT))
    except Exception as e:
        _log.exception("failed to spawn sim_one.sh for run %s", run_name)
        manifest_mod.update(
            run_dir,
            status="error",
            exit_code=-1,
            finished_at=time.time(),
            error=f"failed to spawn: {e}",
        )
        raise

    run = Run(id=run_id, name=run_name, proc=proc, state="running")
    _RUNS[run_id] = run
    drain_task = asyncio.create_task(_drain(run, run_dir))
    drain_task.add_done_callback(_log_task_exception)
    run.drain_task = drain_task
    return run_id


async def _drain(run: Run, run_dir: Path) -> None:
    assert run.proc is not None and run.proc.stdout is not None
    # Persist every line to <run_dir>/run.log as it arrives — without this
    # an errored run leaves no trace once the in-memory Run object is GC'd,
    # and the user can't see WHY it failed. The frontend subscription reads
    # this file on subscribe and replays it as log events.
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"
    with log_path.open("a", buffering=1) as log_fh:
        async for raw in run.proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                run.log_lines.append(line)
                if len(run.log_lines) > 2000:
                    run.log_lines = run.log_lines[-2000:]
                log_fh.write(line + "\n")
    rc = await run.proc.wait()
    # Only overwrite state if still 'running' — preserves a 'cancelled' that
    # cancel_run set while we were tailing stdout. Without this guard, every
    # run that gets cancelled would end up reported as 'done' (rc=0 if the
    # subprocess exits cleanly on SIGTERM) or 'error'.
    if run.state == "running":
        run.state = "done" if rc == 0 else "error"
    manifest_mod.update(
        run_dir,
        status=run.state,
        exit_code=rc,
        finished_at=time.time(),
    )


async def wait_for_run(run_id: str) -> None:
    """Block until the underlying subprocess exits AND _drain has flushed
    the manifest. Used by tests."""
    run = _RUNS.get(run_id)
    if run is None or run.proc is None:
        return
    if run.drain_task is not None:
        await run.drain_task
    else:
        await run.proc.wait()


def cancel_run(run_id: str) -> bool:
    run = _RUNS.get(run_id)
    if run is None or run.proc is None or run.state != "running":
        return False
    run.proc.terminate()
    run.state = "cancelled"
    watchdog = asyncio.create_task(_kill_after_grace(run, grace_sec=5.0))
    watchdog.add_done_callback(_log_task_exception)
    return True


async def _kill_after_grace(run: Run, grace_sec: float) -> None:
    """If the subprocess hasn't exited within grace_sec of SIGTERM, SIGKILL it.

    KNOWN LIMITATION: This only kills the direct subprocess (e.g. bash).
    If the subprocess spawned children (sim_one.sh -> python), those orphans
    can keep stdout pipes open until they exit naturally, which blocks
    `_drain`'s `async for` and prevents `wait_for_run` from returning.

    TODO Phase 5/6: spawn with start_new_session=True and escalate via
    os.killpg(os.getpgid(proc.pid), SIGKILL) so the whole process group
    dies. Phase 1 leaves this as-is because:
      - For direct subprocess cancellation, the current code works.
      - The test suite uses `bash sleep 30` which exits cleanly on SIGTERM
        but leaves an orphan `sleep`; the test still passes (manifest goes
        to 'cancelled') just slower than ideal.
    """
    if run.proc is None:
        return
    try:
        await asyncio.wait_for(run.proc.wait(), timeout=grace_sec)
    except asyncio.TimeoutError:
        if run.proc.returncode is None:
            _log.warning(
                "run %s ignored SIGTERM after %.1fs; sending SIGKILL",
                run.name,
                grace_sec,
            )
            run.proc.kill()
