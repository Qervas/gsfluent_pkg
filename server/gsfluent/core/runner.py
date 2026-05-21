"""Subprocess wrapper for server-side simulation.

Under the split-topology deployment, this runs on the GPU server
next to the canonical sim core. One Run = one subprocess spawn of a shell
wrapper (`server/tools/run_sim.sh` by default) that orchestrates:
    1. The canonical MPM sim (`gs_simulation_building.py`)
    2. The fuse step (`server/tools/fuse_to_full_ply.py`)
After the run exits cleanly, runner.py kicks off `batch_convert_to_npz.py`
to rebuild the .npz cache so the client sync daemon picks it up.

The wrapper path + interpreter are env-overridable so the runner doesn't
hardcode the server's directory layout:

    GSFLUENT_SIM_SCRIPT_RUNNER  path to the shell wrapper invoked per run
                                (default: <PKG_ROOT>/server/tools/run_sim.sh)
    GSFLUENT_NPZ_REBUILD        if "1" (default), trigger .npz build after
                                run completion. Set to "0" if you'd rather
                                build manually.

The wrapper receives:
    $1            model_dir
    --config      recipe JSON written by this runner
    --particles   particle count
    --output      run name (output dir under work/library/sequences/)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
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

# Resolved at module load. Settable via env var so deployment can point
# at a server-specific wrapper without code changes.
SIM_SCRIPT_RUNNER = Path(os.environ.get(
    "GSFLUENT_SIM_SCRIPT_RUNNER",
    str(PKG_ROOT / "server" / "tools" / "run_sim.sh"),
))
# After a successful run, optionally rebuild the .npz cache so the
# client sync daemon notices the new sequence. Off by default in tests.
NPZ_REBUILD_AFTER_RUN = os.environ.get("GSFLUENT_NPZ_REBUILD", "1") == "1"
# Delete the per-frame sim plys + fused plys after the npz is built.
# The .npz is the canonical artifact downstream (viser_headless + sync
# daemon both consume it); the intermediate plys total ~6 GB per run
# (1.5 GB sim plys + 4.5 GB fused plys) so a few runs eat the disk
# fast. Default ON; set GSFLUENT_KEEP_PLYS=1 to preserve them (e.g.
# when iterating on the fuse step or debugging a single frame).
CLEANUP_INTERMEDIATES = os.environ.get("GSFLUENT_KEEP_PLYS", "0") != "1"

# Phase 1.5: point at the library so manifest.json + run.log + recipe.json
# land in the same dir as the wrapper's frame outputs
# (library/sequences/<run>/). Tests monkeypatch this attribute to a tmp dir,
# which keeps working.
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
    """Translate model-local sim_area to world coords when the recipe says so.

    The sim core expects sim_area in absolute world coords (the canonical
    R7.M_jelly_cluster shape: [3440, 3480, 29030, 29060, -25, 35] for a
    building near world (3460, 29045, 5)). Workbench recipes ship portable
    model-local bounds (e.g. [-30, 30, -10, 10, -2, 45]); we translate
    those to the actual model's location at run-start so the same recipe
    can run on any model.

    The recipe MUST be explicit about which frame its sim_area is in:
        "sim_area_frame": "model"   → translate by model's bbox center
        "sim_area_frame": "world"   → leave alone (or absent — that's the
                                        default for back-compat with
                                        legacy world-coord recipes that
                                        predate this field)

    The previous version used a |value| <= 200 heuristic to guess
    model-vs-world. That misfired silently for legitimately-small
    world-coord recipes (e.g. a scene centered near origin in a
    normalized COLMAP), translating them into nonsense. Now-required
    explicit declaration removes the guesswork."""
    out = dict(recipe_data)
    sim_area = out.get("sim_area")
    if not sim_area or len(sim_area) != 6:
        return out
    frame = out.get("sim_area_frame", "world")
    if frame == "world":
        return out
    if frame != "model":
        _log.warning(
            "recipe has unknown sim_area_frame=%r (expected 'model'|'world'); "
            "treating as world", frame,
        )
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


def _read_model_bbox(model_dir: Path) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    """Read the model's highest-iteration point_cloud.ply and return its
    axis-aligned bounding box as `((xmin, ymin, zmin), (xmax, ymax, zmax))`.
    Returns None if the ply can't be parsed. Cheap — only the xyz
    columns are touched."""
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
        from plyfile import PlyData
        v = PlyData.read(str(best[1]))["vertex"].data
        import numpy as np
        x = np.asarray(v["x"], dtype=np.float32)
        y = np.asarray(v["y"], dtype=np.float32)
        z = np.asarray(v["z"], dtype=np.float32)
        lo = (float(x.min()), float(y.min()), float(z.min()))
        hi = (float(x.max()), float(y.max()), float(z.max()))
        return (lo, hi)
    except Exception as e:
        _log.warning("failed to read model bbox for %s: %s", model_dir, e)
        return None


def _read_model_bbox_center(model_dir: Path) -> tuple[float, float, float] | None:
    """Centroid of the model's bbox. Used to translate model-local
    sim_area bounds to world coords. Returns None on parse failure;
    caller leaves the recipe untouched in that case."""
    bb = _read_model_bbox(model_dir)
    if bb is None:
        return None
    (xmin, ymin, zmin), (xmax, ymax, zmax) = bb
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _validate_sim_area_intersects_model(
    sim_area: list[float], model_dir: Path,
) -> None:
    """Cheap preflight: ensure the recipe's sim_area (now in world
    coords after translation) actually overlaps the model's bbox. The
    upstream sim filters splats by sim_area and crashes with a cryptic
    `IndexError: min(): Expected reduction dim 0 to have non-zero
    size.` from torch when 0 splats survive the filter. We catch the
    empty-intersection case here and raise a readable error.

    `sim_area` is `[xmin, xmax, ymin, ymax, zmin, zmax]`. No-op if we
    can't read the model bbox (don't block on a flaky read)."""
    if not sim_area or len(sim_area) != 6:
        return
    bb = _read_model_bbox(model_dir)
    if bb is None:
        return
    (mx0, my0, mz0), (mx1, my1, mz1) = bb
    sx0, sx1, sy0, sy1, sz0, sz1 = (float(x) for x in sim_area)
    overlap = (
        sx0 < mx1 and sx1 > mx0 and
        sy0 < my1 and sy1 > my0 and
        sz0 < mz1 and sz1 > mz0
    )
    if not overlap:
        raise ValueError(
            f"recipe's sim_area does not overlap the model bbox — the sim "
            f"would filter every splat out and crash. "
            f"sim_area (world): x=[{sx0:.2f},{sx1:.2f}] y=[{sy0:.2f},{sy1:.2f}] z=[{sz0:.2f},{sz1:.2f}]; "
            f"model bbox: x=[{mx0:.2f},{mx1:.2f}] y=[{my0:.2f},{my1:.2f}] z=[{mz0:.2f},{mz1:.2f}]. "
            f"Either pick a recipe whose sim_area matches this model's world "
            f"coords, or set `sim_area_frame: \"model\"` in the recipe so the "
            f"runner translates model-local bounds to world."
        )


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
    """Return all runs currently in the process registry.

    Note: `_RUNS` includes runs in any state (running / done / error /
    cancelled) — the registry is GC'd only when the process restarts.
    For "what's still actively running", callers should filter to
    `state == "running"` themselves; for the canonical history of all
    runs ever, walk `lib.SEQUENCES_DIR` instead (that's what
    `/api/runs/history` does).
    """
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

    # Validate the recipe BEFORE writing anything to disk. The old order
    # (write manifest → validate) left a "running"-state zombie on disk
    # when validation raised, polluting History forever.
    #
    # Translate recipe.sim_area from MODEL-LOCAL to WORLD coords if the
    # recipe ships small numbers (workbench-style portable recipe). The
    # sim core expects world coords (matches the canonical R7.M_jelly_cluster
    # convention). Heuristic: if every sim_area value is within ±200, assume
    # model-local and translate by the model's bbox center; otherwise leave
    # alone (assume the recipe author already specified world coords).
    effective_recipe = _translate_sim_area_if_local(recipe_data, model_dir)

    # Preflight: the upstream sim filters splats by sim_area then calls
    # transform2origin on the survivors; if the filter empties the set,
    # torch raises a cryptic `min(): Expected reduction dim 0 to have
    # non-zero size.` Catch the empty-intersection case here with a
    # readable error so the workbench can surface it cleanly.
    _validate_sim_area_intersects_model(
        effective_recipe.get("sim_area", []), model_dir,
    )

    # Only now do we touch disk.
    run_dir = FUSED_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_mod.write_initial(run_dir, run_name, model_dir, recipe_source_name, particles)
    manifest_mod.write_recipe(run_dir, recipe_data)

    # Write the merged effective recipe to a temp file the wrapper consumes.
    recipe_path = run_dir / "_effective_recipe.json"
    recipe_path.write_text(json.dumps(effective_recipe, indent=2))

    if not SIM_SCRIPT_RUNNER.is_file():
        raise FileNotFoundError(
            f"sim wrapper not found: {SIM_SCRIPT_RUNNER}. "
            "Adapt server/tools/run_sim.sh to your server, or set "
            "$GSFLUENT_SIM_SCRIPT_RUNNER to point at your wrapper."
        )

    cmd = [
        "bash", str(SIM_SCRIPT_RUNNER),
        str(model_dir),
        "--config", str(recipe_path),
        "--particles", str(particles),
        "--output", run_name,
    ]
    try:
        proc = await _spawn(*cmd, stdout=PIPE, stderr=STDOUT, cwd=str(PKG_ROOT))
    except Exception as e:
        _log.exception("failed to spawn sim wrapper for run %s", run_name)
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
    # Outer try/finally guarantees a final manifest write even on IO
    # errors mid-drain (broken pipe, decode failure, disk-full). Without
    # this the run stays "running" forever in the UI when stdout
    # iteration raises, because the post-drain manifest update is dead
    # code on that path.
    rc: int = -1
    try:
        with log_path.open("a", buffering=1) as log_fh:
            try:
                async for raw in run.proc.stdout:
                    line = raw.decode(errors="replace").rstrip()
                    if line:
                        run.log_lines.append(line)
                        if len(run.log_lines) > 2000:
                            run.log_lines = run.log_lines[-2000:]
                        log_fh.write(line + "\n")
            except Exception as e:
                _log.exception("drain loop crashed for run %s", run.name)
                log_fh.write(f"[runner] drain crashed: {e}\n")
        rc = await run.proc.wait()
    except Exception as e:
        _log.exception("drain wrapper crashed for run %s", run.name)
        if run.state == "running":
            run.state = "error"
        manifest_mod.update(
            run_dir,
            status=run.state,
            exit_code=-1,
            finished_at=time.time(),
            error=f"drain crashed: {e}",
        )
        return
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

    # On a successful run, rebuild the .npz cache so the client sync
    # daemon notices the new sequence on its next poll. We invoke
    # batch_convert_to_npz.py as a separate subprocess (rather than
    # importing it) so any plyfile / numpy work it does runs in its own
    # process — keeps the API server's memory profile clean. Logged to
    # run.log so failures are visible in the same WS replay.
    if run.state == "done" and NPZ_REBUILD_AFTER_RUN:
        try:
            await _rebuild_npz(run.name, run_dir, log_path)
        except Exception as e:
            _log.warning("post-run .npz rebuild failed for %s: %s", run.name, e)

    # Write _meta.json for the freshly-produced library sequence. Without
    # this the client's /api/sequences sees a dir with no metadata and
    # surfaces it as source="unknown" — and the sync daemon has nothing
    # canonical to mirror. Done unconditionally on success so the entry
    # is well-formed even if the .npz rebuild failed (manual rebuild
    # later still leaves the sequence renderable).
    if run.state == "done":
        try:
            _write_sequence_meta(run.name, run_dir)
        except Exception as e:
            _log.warning("post-run _meta.json write failed for %s: %s", run.name, e)

    # Now that npz + _meta are in place, the per-frame plys are dead
    # weight. Drop them so the next run has disk to land on.
    if run.state == "done" and CLEANUP_INTERMEDIATES:
        try:
            _cleanup_intermediates(run.name, run_dir, log_path)
        except Exception as e:
            _log.warning("post-run cleanup failed for %s: %s", run.name, e)


def _cleanup_intermediates(run_name: str, run_dir: Path, log_path: Path) -> None:
    """Delete the per-frame .ply files now that the .npz is built.

    Removes:
      - <SIM_HOME>/output/<run_name>/   (raw MPM sim plys, ~1.5 GB)
      - <run_dir>/frames/                (fused per-frame plys, ~4.5 GB)

    Keeps the run dir itself + manifest.json + _meta.json + recipe
    files so the library entry is still discoverable. Appends a one-
    line audit to run.log so the WS subscriber sees what was freed.
    """
    import shutil
    bytes_freed = 0

    sim_home = Path(os.environ.get(
        "GSFLUENT_SIM_HOME", "${GSFLUENT_SIM_HOME}",
    ))
    sim_output = sim_home / "output" / run_name
    if sim_output.is_dir():
        bytes_freed += sum(p.stat().st_size for p in sim_output.rglob("*") if p.is_file())
        shutil.rmtree(sim_output, ignore_errors=True)

    frames_dir = run_dir / "frames"
    if frames_dir.is_dir():
        bytes_freed += sum(p.stat().st_size for p in frames_dir.rglob("*") if p.is_file())
        shutil.rmtree(frames_dir, ignore_errors=True)

    gb = bytes_freed / (1024 ** 3)
    msg = f"[cleanup] freed {gb:.2f} GB of intermediate plys for {run_name}\n"
    try:
        with open(log_path, "a") as f:
            f.write(msg)
    except OSError:
        pass
    _log.info(msg.strip())


def _write_sequence_meta(run_name: str, run_dir: Path) -> None:
    """Write the canonical `_meta.json` for a completed sim sequence.

    Pulls `model_dir` back out of `manifest.json` (saved at start_run)
    and reads frame_0000.ply for n_splats + bbox_initial. Frame count is
    a directory walk of `frames/`. Source path is hostname-qualified so
    the client can later distinguish "produced on the sim server" from a
    locally-imported sequence with the same name."""
    import socket
    frames_dir = run_dir / "frames"
    frame0 = frames_dir / "frame_0000.ply"
    frame_count = sum(1 for p in frames_dir.glob("frame_*.ply")) if frames_dir.is_dir() else 0
    if frame0.is_file():
        n_splats, bbox = lib.read_ply_bbox_and_count(frame0)
    else:
        n_splats, bbox = None, None
    model_ref: Optional[str] = None
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text())
            md = data.get("model_dir")
            if isinstance(md, str) and md:
                model_ref = Path(md).name
        except (json.JSONDecodeError, OSError):
            pass
    lib.Sequence.write_meta(
        name=run_name,
        source="sim",
        source_path=f"{socket.gethostname()}:{run_dir}",
        model_ref=model_ref,
        frame_count=frame_count,
        n_splats=n_splats,
        bbox_initial=bbox,
        coord_convention="z-up",
        first_frame_full=True,
    )


async def _rebuild_npz(run_name: str, run_dir: Path, log_path: Path) -> None:
    """Invoke `server/tools/batch_convert_to_npz.py <run_name>` as a subprocess.

    Output is appended to the same run.log so a WS subscriber sees the
    cache build progress as part of the run timeline. We don't fail the
    run if the rebuild fails — the sequence still exists on disk, just
    isn't viser-playable until the cache is built manually."""
    converter = PKG_ROOT / "server" / "tools" / "batch_convert_to_npz.py"
    if not converter.is_file():
        return
    cmd = [sys.executable, str(converter), run_name]
    try:
        proc = await _spawn(*cmd, stdout=PIPE, stderr=STDOUT, cwd=str(PKG_ROOT))
    except Exception as e:
        with log_path.open("a") as fh:
            fh.write(f"[runner] npz rebuild spawn failed: {e}\n")
        return
    assert proc.stdout is not None
    with log_path.open("a", buffering=1) as fh:
        fh.write(f"[runner] building .npz cache for {run_name}…\n")
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                fh.write(f"[npz] {line}\n")
    rc = await proc.wait()
    with log_path.open("a") as fh:
        if rc == 0:
            fh.write(f"[runner] .npz cache built for {run_name}\n")
        else:
            fh.write(f"[runner] .npz cache build exited {rc} for {run_name}\n")


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
    If the subprocess spawned children (run_sim.sh -> python), those orphans
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
