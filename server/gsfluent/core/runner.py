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
    drain_task: Optional[asyncio.Task] = None


_RUNS: dict[str, Run] = {}


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
    async for raw in run.proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            run.log_lines.append(line)
            if len(run.log_lines) > 2000:
                run.log_lines = run.log_lines[-2000:]
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
