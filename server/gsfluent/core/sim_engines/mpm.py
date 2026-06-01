"""MPMSimulationEngine - production MPM sim orchestration.

Absorbs the orchestration logic previously living in
server/tools/run_sim.sh:
  - preflight checks (sim_home dir, sim python interpreter, GPU)
  - spawn the MPM sim subprocess in a new process group
  - spawn the fuse subprocess in the same group
  - classify stderr against operator-tunable YAML patterns on failure

The wall-time timeout and signal-escalation logic live in
core/run_manager.py (the outer asyncio.wait_for + killpg ladder).
This engine emits structured events through the on_event EventEmitter:

  sim.preflight_ok
  sim.gpu_autopicked  (gpu_index, util, free_mib)  — auto-GPU selection
  sim.spawned         (pid, pgid, argv)
  sim.completed       (returncode, duration_sec, n_frames)
  fuse.spawned        (pid, argv)
  fuse.completed      (returncode, duration_sec)

Errors raised:
  SimEnvMissingError, SimInterpreterMissingError, GPUUnavailableError
  SimGpuOomError, SimUnstableRecipeError, SimCrashedError
  (SimWallTimeExceededError is raised by the RunManager, not here.)
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from asyncio.subprocess import create_subprocess_exec as _spawn  # alias for grep-safety
from dataclasses import dataclass
from pathlib import Path

from collections.abc import Callable

import yaml

from gsfluent._paths import PKG_ROOT
from gsfluent.core import manifest as _manifest
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    ModelRef,
    SimCrashedError,
    SimEnvMissingError,
    SimGpuOomError,
    SimInterpreterMissingError,
    SimResult,
    SimUnstableRecipeError,
    ValidatedRecipe,
)

# ---------- stderr classifier --------------------------------------------


@dataclass(frozen=True)
class MPMErrorPattern:
    """One stderr-pattern -> error_kind mapping."""
    error_kind: str
    regex_source: str
    case_insensitive: bool
    description: str
    compiled: re.Pattern[str]


def _default_patterns_path() -> Path:
    return Path(__file__).parent / "mpm_error_patterns.yaml"


def load_error_patterns(path: Path | None = None) -> list[MPMErrorPattern]:
    """Load the operator-tunable stderr pattern file. Defaults to
    server/gsfluent/core/sim_engines/mpm_error_patterns.yaml.
    """
    p = path if path is not None else _default_patterns_path()
    try:
        raw = yaml.safe_load(p.read_text())
    except Exception as e:
        # This is fatal for the engine — better to surface it loudly.
        raise RuntimeError(f"Failed to load error patterns from {p}: {e}") from e

    out: list[MPMErrorPattern] = []
    for entry in raw.get("patterns", []):
        flags = re.IGNORECASE if entry.get("case_insensitive", False) else 0
        out.append(
            MPMErrorPattern(
                error_kind=entry["error_kind"],
                regex_source=entry["regex"],
                case_insensitive=entry.get("case_insensitive", False),
                description=entry.get("description", ""),
                compiled=re.compile(entry["regex"], flags),
            )
        )
    return out


def classify_stderr(
    stderr: str, patterns: list[MPMErrorPattern]
) -> str | None:
    """Return the first matching error_kind, or None if no pattern matches.

    Scans the entire stderr (not just the tail) - sim errors can fire
    early and be followed by unrelated output.
    """
    if not stderr:
        return None
    for pat in patterns:
        if pat.compiled.search(stderr) is not None:
            return pat.error_kind
    return None


def _kind_to_exception(kind: str, message: str) -> Exception:
    """Map a classifier kind string to its exception class."""
    if kind == "sim.gpu_oom":
        return SimGpuOomError(message)
    if kind == "sim.unstable_recipe":
        return SimUnstableRecipeError(message)
    return SimCrashedError(message)


def check_sim_stability(
    *,
    n_sim: int,
    n_fused: int,
    allowed_nonfinite: int,
    expected_frames: int | None = None,
) -> str | None:
    """Detect a diverged simulation from a frame-count shortfall.

    Two divergence signatures are caught:

    1. **Fuser drop** (``n_fused < n_sim``): the fuser silently skips sim
       frames whose particle positions are non-finite (NaN/Inf), so fewer
       fused frames than sim frames means the MPM solver produced unusable
       (NaN) particles mid-run.

    2. **Sim truncation** (``n_sim < expected_frames``): the MPM solver can
       also blow up and terminate *early*, writing fewer sim frames than the
       recipe requested. In that case ``n_sim == n_fused`` (the fuser keeps
       every frame the sim emitted), so signature 1 misses it — yet the
       sequence is still a silently-truncated, diverged run. ``expected_frames``
       is the count the recipe asked for; when provided and the sim wrote
       fewer than that, the run is flagged.

    Returns a human-readable error message when either signature trips (so
    the run fails loudly instead of being marked done with a truncated
    sequence), else ``None``. ``n_sim <= 0`` is a different failure path
    (no sim output at all) handled elsewhere.

    ``allowed_nonfinite`` tolerates up to that many dropped/missing frames
    (applies to both signatures) before flagging.
    """
    if n_sim <= 0:
        return None

    # Signature 2: the sim itself stopped early (n_sim == n_fused but short
    # of what the recipe requested). Checked first because a truncated sim
    # is the more common production divergence and the more misleading
    # "done" result. expected_frames is None for legacy callers / tests that
    # only exercise signature 1.
    if expected_frames is not None and expected_frames > 0:
        missing = expected_frames - n_sim
        if missing > allowed_nonfinite:
            return (
                f"simulation diverged: only {n_sim} of {expected_frames} "
                f"requested frames were produced before the solver stopped "
                f"({missing} missing). The recipe is numerically unstable."
            )

    # Signature 1: the fuser dropped NaN/Inf frames the sim did emit.
    dropped = n_sim - n_fused
    if dropped > allowed_nonfinite:
        return (
            f"simulation diverged: {dropped} of {n_sim} frames had non-finite "
            f"(NaN/Inf) positions and were dropped ({n_fused} usable). The "
            f"recipe is numerically unstable."
        )
    return None


# ---------- the engine ---------------------------------------------------


class MPMSimulationEngine:
    """Concrete SimulationEngine for the MPM sim (warp + taichi + torch).

    Spawns two subprocesses per run() call, sequentially (sim is awaited to
    completion before the fuse starts):
      1. The canonical MPM sim (gs_simulation_building.py)
      2. The fuse stage (server/tools/fuse_to_full_ply.py)
    Each gets its own new process group (the sim's pg is already gone by the
    time the fuse spawns, so they can't share one); killpg(pgid) on
    cancel/timeout targets whichever stage is currently running.

    Construction:
        eng = MPMSimulationEngine(
            sim_home=Path("/path/to/GaussianFluent"),
            sim_python="/path/to/sim-env/bin/python",
            sim_env="physics",   # optional conda env name
            require_gpu=True,
            patterns_path=None,  # default: bundled yaml
        )
    """

    def __init__(
        self,
        *,
        sim_home: Path,
        sim_python: str,
        sim_env: str | None = None,
        require_gpu: bool = True,
        patterns_path: Path | None = None,
        sim_fast: bool = False,
    ) -> None:
        self._sim_home = sim_home
        self._sim_python = sim_python
        self._sim_env = sim_env
        self._require_gpu = require_gpu
        self._sim_fast = sim_fast
        self._patterns = load_error_patterns(path=patterns_path)

    # ---- preflight ------------------------------------------------------

    async def preflight(self) -> None:
        """Raise typed error if environment cannot run a sim.

        Checked in order: sim_home dir exists, sim_python on PATH /
        absolute path resolvable, optional GPU reachability.
        """
        if not self._sim_home.is_dir():
            raise SimEnvMissingError(
                f"GSFLUENT_SIM_HOME directory not found: {self._sim_home}"
            )

        resolved_python = (
            shutil.which(self._sim_python)
            if not os.path.isabs(self._sim_python)
            else (self._sim_python if Path(self._sim_python).is_file() else None)
        )
        if not resolved_python:
            raise SimInterpreterMissingError(
                f"sim python interpreter not found: {self._sim_python}"
            )

        if self._require_gpu and not _gpu_reachable():
            raise GPUUnavailableError(
                "nvidia-smi reports no CUDA-capable device"
            )

    # ---- run ------------------------------------------------------------

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        """Spawn sim + fuse and wait for both. Wall-time + cancel handling
        happens in the caller (RunManager), which wraps the awaited task
        in asyncio.wait_for and on timeout / cancel does killpg on the
        process group recorded in the sim.spawned event.

        Emits sim.* + fuse.* events through on_event. Returns SimResult
        on success, raises classified SimError on failure.
        """
        on_event.info("sim.preflight_ok", model=model.name, particles=recipe.get("particle_count"))

        # Resolve paths the same way run_sim.sh did so we keep
        # bug-for-bug compatibility on the directory layout.
        run_name = recipe.get("_run_name") or output_dir.name
        sim_output_dir = self._sim_home / "output" / run_name
        sim_ply_dir = sim_output_dir / "simulation_ply"
        library_seq_dir = PKG_ROOT / "work" / "library" / "sequences" / run_name
        fused_dir = library_seq_dir / "frames"

        on_event.debug("sim.dirs.ensure", paths=[str(sim_output_dir), str(library_seq_dir), str(fused_dir)])
        sim_output_dir.mkdir(parents=True, exist_ok=True)
        library_seq_dir.mkdir(parents=True, exist_ok=True)
        fused_dir.mkdir(parents=True, exist_ok=True)

        # Find the highest-iteration reference ply under model/point_cloud/.
        reference_ply = _find_reference_ply(model.path)
        if reference_ply is None:
            on_event.error("sim.no_reference_ply", model_path=str(model.path))
            raise SimCrashedError(
                f"no reference ply under {model.path}/point_cloud/"
            )
        on_event.debug("sim.reference_ply.selected", path=str(reference_ply))

        # Preserve the merged recipe.json early so a sim crash doesn't lose it.
        try:
            config_path = library_seq_dir / "recipe.json"
            import json
            config_path.write_text(json.dumps(recipe, indent=2))
        except Exception as e:  # noqa: BLE001
            on_event.error("sim.manifest.write_failed", path=str(config_path), error=str(e))

        particles = int(recipe.get("particle_count", 200_000))
        bcs = recipe.get("boundary_conditions", [])
        on_event.debug("sim.recipe.summary", n_bcs=len(bcs), has_frame_num=bool(recipe.get("frame_num")))

        # Write the run manifest BEFORE either subprocess spawns.
        recipe_source = str(recipe.get("_recipe_source_name") or "")
        try:
            _manifest.write_initial(
                run_dir=library_seq_dir,
                run_name=run_name,
                model_dir=model.path,
                recipe_source=recipe_source,
                particles=particles,
            )
        except Exception as e:  # noqa: BLE001
            on_event.error("sim.manifest.write_failed", error=str(e))

        # Open the on-disk run.log
        log_path = library_seq_dir / "run.log"
        log_fh = None
        try:
            log_fh = log_path.open("a", buffering=1, encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            on_event.error("sim.run_log.open_failed", path=str(log_path), error=str(e))
            # Continue without file logging — we'll still capture in memory for classification.

        t0 = time.monotonic()
        run_status = "running"
        run_error: dict[str, str] | None = None
        try:
            on_event.info("sim.stage.start", stage="sim", run_name=run_name)

            # ---- stage 1: MPM sim --------------------------------------

            sim_argv = self._build_sim_argv(
                model_dir=model.path,
                sim_output_dir=sim_output_dir,
                config_path=config_path,
                particles=particles,
            )
            on_event.debug("sim.command.built", argv=sim_argv)

            # Auto-select the least-busy GPU on a shared box, overriding the
            # static .env CUDA_VISIBLE_DEVICES pin for this sim only. None ->
            # inherit the pin unchanged (every failure / disabled path).
            on_event.debug("sim.gpu_selection.start")
            gpu_overlay = _resolve_sim_gpu_env(on_event=on_event)
            # Per-run boundary mode flows recipe -> solver via env (the solver
            # reads GSFLUENT_BOUNDARY_MODE in its __init__). Default "drop".
            boundary_mode = str(recipe.get("boundary_mode", "drop"))
            sim_env = {**os.environ, "GSFLUENT_BOUNDARY_MODE": boundary_mode}
            if gpu_overlay:
                sim_env.update(gpu_overlay)
            on_event.debug(
                "sim.env.resolved",
                has_gpu_overlay=bool(gpu_overlay),
                boundary_mode=boundary_mode,
            )

            sim_proc = await self._spawn_in_new_pg(
                argv=sim_argv,
                cwd=str(self._sim_home),
                env=sim_env,
            )
            pgid = os.getpgid(sim_proc.pid)
            pid_starttime = _read_pid_starttime(sim_proc.pid)
            on_event.info(
                "sim.spawned",
                pid=sim_proc.pid,
                pgid=pgid,
                pid_starttime=pid_starttime,
                argv=sim_argv,
            )

            on_event.info("sim.waiting_for_process", pid=sim_proc.pid)

            sim_stderr_chunks: list[str] = []
            sim_rc = await _wait_capturing_stderr(
                sim_proc, sim_stderr_chunks, log_file=log_fh,
            )
            on_event.debug("sim.process_exited", pid=sim_proc.pid, rc=sim_rc)

            sim_duration = time.monotonic() - t0
            on_event.info(
                "sim.completed",
                returncode=sim_rc,
                duration_sec=round(sim_duration, 2),
                n_expected_frames=recipe.get("frame_num"),
            )
            if sim_rc != 0:
                joined = "".join(sim_stderr_chunks)
                on_event.debug("sim.stderr.tail", tail=joined[-2000:])
                kind = classify_stderr(joined, self._patterns)
                msg = (
                    f"sim exited with rc={sim_rc} after {sim_duration:.1f}s; "
                    f"classified as {kind or 'sim.crashed'}"
                )
                # Keep the canonical `error.<kind>` taxonomy name — the run
                # manager's de-dup guard matches on the `error.` prefix, and
                # this is the boundary event other tooling keys off. The new
                # ERROR level is orthogonal to the dotted event name.
                on_event.error(
                    f"error.{kind or 'sim.crashed'}",
                    returncode=sim_rc,
                    stderr_tail=joined[-2000:],
                )
                run_error = {"kind": kind or "sim.crashed", "message": msg}
                raise _kind_to_exception(kind or "sim.crashed", msg)

            on_event.info("sim.stage.complete", stage="sim", duration_sec=round(sim_duration, 2))

            # ---- stage 2: fuse -----------------------------------------
            on_event.info("sim.stage.start", stage="fuse", run_name=run_name)

            # Build fuse command (this is where we do the heavy KNN + Kabsch work)
            fuse_argv = self._build_fuse_argv(
                reference_ply=reference_ply,
                sim_ply_dir=sim_ply_dir,
                fused_dir=fused_dir,
            )
            on_event.debug("fuse.command.built", argv=fuse_argv)

            t1 = time.monotonic()
            # The sim has already exited (awaited above), so its process
            # group is gone — the fuse cannot join it (setpgid into a dead
            # pg raises in the preexec_fn). The two stages run sequentially,
            # so give the fuse its own process group; killpg on cancel /
            # timeout targets fuse_pgid here.
            try:
                fuse_proc = await self._spawn_in_new_pg(
                    argv=fuse_argv,
                    cwd=str(PKG_ROOT),
                )
            except Exception as e:
                on_event.error("fuse.spawn_failed", argv=fuse_argv, error=str(e))
                raise

            fuse_pgid = os.getpgid(fuse_proc.pid)
            on_event.info("fuse.spawned", pid=fuse_proc.pid, pgid=fuse_pgid, argv=fuse_argv)
            on_event.info("fuse.waiting_for_process", pid=fuse_proc.pid)

            fuse_stderr_chunks: list[str] = []
            fuse_rc = await _wait_capturing_stderr(
                fuse_proc, fuse_stderr_chunks, log_file=log_fh,
            )
            on_event.debug("fuse.process_exited", pid=fuse_proc.pid, rc=fuse_rc)

            fuse_duration = time.monotonic() - t1
            if fuse_rc != 0:
                joined = "".join(fuse_stderr_chunks)
                on_event.error(
                    "error.fuse.crashed",
                    returncode=fuse_rc,
                    stderr_tail=joined[-2000:],
                )
                msg = (
                    f"fuse exited with rc={fuse_rc} after {fuse_duration:.1f}s; "
                    f"stderr tail: {joined[-500:]}"
                )
                run_error = {"kind": "fuse.crashed", "message": msg}
                raise SimCrashedError(msg)

            # Count fused output before announcing completion — fuse.completed
            # reports n_frames, so it has to run after the glob.
            on_event.debug("fuse.counting_output_frames")
            n_frames = sum(1 for _ in fused_dir.glob("frame_*.ply"))
            on_event.debug("fuse.output_frames_counted", n_frames=n_frames)

            on_event.info(
                "fuse.completed",
                returncode=fuse_rc,
                duration_sec=round(fuse_duration, 2),
                n_frames=n_frames,
            )

            # Fail loudly on a diverged sim. The fuser silently skips frames
            # whose sim positions are NaN/Inf, so fewer fused frames than sim
            # frames means the MPM solver blew up mid-run. Without this guard
            # the run would be marked `done` with a truncated sequence — a
            # silent corruption. Tolerance configurable via
            # GSFLUENT_ALLOWED_NONFINITE_FRAMES (default 0 = any drop fails).
            on_event.debug("sim.stability_check.start")

            n_sim_frames = sum(1 for _ in sim_ply_dir.glob("sim_*.ply"))
            allowed = int(os.environ.get("GSFLUENT_ALLOWED_NONFINITE_FRAMES", "0"))
            expected_frames = _expected_sim_frames(recipe)

            unstable = check_sim_stability(
                n_sim=n_sim_frames,
                n_fused=n_frames,
                allowed_nonfinite=allowed,
                expected_frames=expected_frames,
            )
            on_event.debug(
                "sim.stability_check.done",
                n_sim=n_sim_frames,
                n_fused=n_frames,
                expected=expected_frames,
                allowed_nonfinite=allowed,
                unstable=bool(unstable),
            )
            if unstable:
                on_event.error(
                    "error.sim.unstable_recipe",
                    n_sim=n_sim_frames,
                    n_fused=n_frames,
                    dropped=n_sim_frames - n_frames,
                    allowed_nonfinite=allowed,
                    message=unstable,
                )
                run_error = {"kind": "sim.unstable_recipe", "message": unstable}
                raise SimUnstableRecipeError(unstable)

            on_event.info(
                "sim.pipeline.complete",
                total_duration_sec=round(time.monotonic() - t0, 2),
                sim_duration_sec=round(sim_duration, 2),
                fuse_duration_sec=round(fuse_duration, 2),
                final_frames=n_frames,
            )

            run_status = "done"
            return SimResult(
                frames_dir=fused_dir,
                n_frames=n_frames,
                duration_sec=time.monotonic() - t0,
            )
        except BaseException as exc:
            # Cancellation (CancelledError) + classified failures both flow
            # through here. We mark the manifest as failed so the history
            # view reflects reality and the orphan-detection in the API
            # surfaces the failure instead of leaving "running" forever.
            if run_status == "running":
                run_status = "failed"
                # Only shout "unexpected" for genuinely unclassified failures.
                # Cancellation is normal teardown (the run manager emits
                # run.cancelled), and a classified SimError already emitted its
                # own error.* boundary event above (run_error is set) — emitting
                # here too would double-log at ERROR.
                if not isinstance(exc, asyncio.CancelledError) and run_error is None:
                    on_event.error(
                        "sim.unexpected_crash",
                        error_type=type(exc).__name__,
                        error=str(exc),
                        run_name=run_name,
                    )
            raise
        finally:
            # Final manifest update — very important for history and recovery
            try:
                fields = {"status": run_status, "finished_at": time.time()}
                if run_error is not None:
                    fields["error"] = run_error
                _manifest.update(library_seq_dir, **fields)
            except Exception as e:
                on_event.error(
                    "sim.manifest.final_update_failed",
                    run_name=run_name,
                    error=str(e),
                )
            try:
                if log_fh is not None:
                    log_fh.close()
            except Exception:
                pass

    # ---- helpers --------------------------------------------------------

    def _build_sim_argv(
        self,
        *,
        model_dir: Path,
        sim_output_dir: Path,
        config_path: Path,
        particles: int,
    ) -> list[str]:
        extras: list[str] = []
        if self._sim_fast:
            # NOTE: the fast path used to also pass --no_cfl_override, which
            # tells the solver to skip its `substep_dt = min(recipe_dt, cfl_dt)`
            # clamp and run the recipe's raw substep_dt verbatim. That removed
            # the only time-step safety net: a recipe whose substep_dt exceeds
            # the CFL limit would diverge silently. The clamp ONLY ever tightens
            # (never relaxes) substep_dt, so always letting the solver clamp is
            # safe — for a recipe already within CFL it is a no-op, and the only
            # cost is a single CFL computation at sim setup. --graph_capture is
            # an orthogonal perf win (fuses the substep loop into one CUDA graph)
            # with no bearing on time-step stability, so it stays.
            extras += ["--graph_capture"]
        return [
            self._sim_python,
            "gs_simulation/watermelon/gs_simulation_building.py",
            "--model_path", str(model_dir),
            "--output_path", str(sim_output_dir),
            "--config", str(config_path),
            "--target_particles", str(particles),
            # --output_rot emits each particle's GPU-computed polar rotation
            # (compute_R_from_F) as a quaternion per frame. The fuser consumes
            # it (Track-1 rotation) — exact per-particle R, no CPU Kabsch SVD.
            # ~16 bytes/particle/frame extra; falls back to CPU Kabsch if a sim
            # build predates the --output_rot patch (no rot_* cols emitted).
            "--output_ply", "--output_rot", "--async_io",
            *extras,
        ]

    def _build_fuse_argv(
        self,
        *,
        reference_ply: Path,
        sim_ply_dir: Path,
        fused_dir: Path,
    ) -> list[str]:
        # This is the heavy post-processing step (KNN skinning + Kabsch rotation)
        return [
            self._sim_python,
            str(PKG_ROOT / "server" / "tools" / "fuse_to_full_ply.py"),
            "--reference_ply", str(reference_ply),
            "--sim_dir", str(sim_ply_dir),
            "--out_dir", str(fused_dir),
            "--knn", "8",
            "--no_zup",
        ]

    async def _spawn_in_new_pg(
        self,
        argv: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        """Launch the sim child in a brand-new process group.

        start_new_session=True triggers setsid() in the child between
        fork and the target program load. The child becomes the leader
        of a fresh session AND process group. Any further children it
        spawns inherit that group, so killpg(pgid, SIG) reaches all of
        them with a single call.

        ``env`` is the child's full environment. When None the child
        inherits the parent's environment unchanged (the historical
        behaviour — including the static .env CUDA_VISIBLE_DEVICES pin).
        The sim stage passes ``{**os.environ, "CUDA_VISIBLE_DEVICES": "N"}``
        to override the pin for an auto-selected GPU on a shared box.
        """
        try:
            return await _spawn(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=env,
            )
        except Exception as e:
            # This is a serious failure — we couldn't even launch the child.
            raise SimCrashedError(f"Failed to spawn process: {argv[0]} ... : {e}") from e


# ---------- module-level helpers -----------------------------------------


# ---------- auto-GPU selection -------------------------------------------
#
# On a shared multi-GPU box the backend's CUDA_VISIBLE_DEVICES is pinned
# once in .env at startup. That repeatedly landed a fresh sim on a GPU that
# had since gone busy, where it crawled ~10x or diverged. Instead of trusting
# the static pin, we query nvidia-smi at *spawn* time and pick the least-busy
# GPU that still has comfortable free memory, overriding CUDA_VISIBLE_DEVICES
# for that one sim subprocess only. Any failure falls back to the inherited
# pin (see _resolve_sim_gpu_env) so GPU selection can never crash a run.

# Default free-memory floor. The sim itself uses ~3 GB, but on a shared box we
# want headroom so a co-tenant's allocation spike doesn't OOM us mid-run.
_DEFAULT_MIN_FREE_MIB = 20 * 1024  # 20 GiB


def pick_free_gpu(nvidia_smi_csv_text: str, min_free_mib: int) -> int | None:
    """Pick the least-busy GPU index that has >= min_free_mib free memory.

    Pure function over the text of:

        nvidia-smi --query-gpu=index,utilization.gpu,memory.free \\
                   --format=csv,noheader,nounits

    which yields one ``<index>, <util%>, <free_MiB>`` row per GPU, e.g.::

        0, 36, 57214
        7, 100, 47483

    Selection: among GPUs whose free memory is at least ``min_free_mib``,
    return the index with the lowest utilization. Ties on utilization break
    toward the GPU with the most free memory, then the lowest index — fully
    deterministic. Returns ``None`` when no GPU qualifies (all busy/full) or
    the input is empty/malformed beyond recovery. Individual unparseable rows
    are skipped, not fatal, so one garbled line never discards the whole query.
    """
    candidates: list[tuple[int, int, int]] = []  # (util, -free, index) sort key
    for line in nvidia_smi_csv_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [c.strip() for c in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            index = int(parts[0])
            util = int(parts[1])
            free_mib = int(parts[2])
        except (ValueError, TypeError):
            continue
        if free_mib < min_free_mib:
            continue
        candidates.append((util, -free_mib, index))
    if not candidates:
        return None
    # Lowest util wins; ties -> most free memory; ties -> lowest index.
    util, neg_free, index = min(candidates)
    return index


def _query_nvidia_smi_csv() -> str | None:
    """Return raw index,util,free CSV from the local nvidia-smi, or None.

    The sim runs on the same host as the backend, so we query locally (the
    same way _gpu_reachable does). Conservative: returns None on any error
    (binary missing, nonzero exit, timeout) so the caller falls back to the
    inherited CUDA_VISIBLE_DEVICES pin.
    """
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return None
    try:
        result = subprocess.run(
            [
                nvsmi,
                "--query-gpu=index,utilization.gpu,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _auto_gpu_enabled() -> bool:
    """Whether auto-GPU selection is on. Default ON; disable-able via env.

    GSFLUENT_AUTO_GPU in {0, false, no, off} (case-insensitive) turns it off,
    restoring the static .env CUDA_VISIBLE_DEVICES pin behaviour.
    """
    raw = os.environ.get("GSFLUENT_AUTO_GPU")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _resolve_sim_gpu_env(
    *,
    on_event: EventEmitter,
    query: Callable[[], str | None] = _query_nvidia_smi_csv,
) -> dict[str, str] | None:
    """Compute the env overlay for the sim subprocess's GPU pin, or None.

    Returns a dict to merge over os.environ (``{"CUDA_VISIBLE_DEVICES": "N"}``)
    when auto-selection succeeds, or ``None`` to mean "inherit the .env pin
    unchanged" — the fallback for every failure mode:

      * GSFLUENT_AUTO_GPU disabled
      * nvidia-smi query failed / unavailable
      * no GPU met the free-memory floor (all busy/full)
      * any unexpected error (defensive: GPU selection never crashes a run)

    Emits ``sim.gpu_autopicked`` on success and ``sim.gpu_autopick_skipped``
    (with a reason) otherwise, so the choice is observable in the event log.
    """
    if not _auto_gpu_enabled():
        on_event.debug("sim.gpu_autopick_skipped", reason="disabled")
        return None
    try:
        min_free_mib = int(
            os.environ.get("GSFLUENT_GPU_MIN_FREE_MIB", _DEFAULT_MIN_FREE_MIB)
        )
    except (TypeError, ValueError):
        min_free_mib = _DEFAULT_MIN_FREE_MIB
    try:
        csv_text = query()
        if not csv_text:
            on_event.debug("sim.gpu_autopick_skipped", reason="query_failed")
            return None
        index = pick_free_gpu(csv_text, min_free_mib)
        if index is None:
            on_event.info(
                "sim.gpu_autopick_skipped",
                reason="no_gpu_qualified",
                min_free_mib=min_free_mib,
            )
            return None
        # Best-effort enrich the event with the picked GPU's stats.
        util, free_mib = _gpu_stats_for_index(csv_text, index)
        on_event.info(
            "sim.gpu_autopicked",
            gpu_index=index,
            util=util,
            free_mib=free_mib,
            min_free_mib=min_free_mib,
        )
        return {"CUDA_VISIBLE_DEVICES": str(index)}
    except Exception as exc:  # noqa: BLE001 — selection must never crash a run
        on_event.error("sim.gpu_autopick_skipped", reason=f"error:{exc!r}")
        return None


def _gpu_stats_for_index(
    csv_text: str, index: int
) -> tuple[int | None, int | None]:
    """Return (util, free_mib) for `index` from the CSV, or (None, None)."""
    for line in csv_text.splitlines():
        parts = [c.strip() for c in line.strip().split(",")]
        if len(parts) != 3:
            continue
        try:
            if int(parts[0]) == index:
                return int(parts[1]), int(parts[2])
        except (ValueError, TypeError):
            continue
    return None, None


def _gpu_reachable() -> bool:
    """Return True iff nvidia-smi reports at least one CUDA-capable device.

    Conservative: returns False on any error (nvidia-smi missing, no
    devices listed, permission denied). MPMSimulationEngine treats
    False as GPUUnavailableError when require_gpu=True.
    """
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return False
    try:
        result = subprocess.run(
            [nvsmi, "-L"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    # `nvidia-smi -L` prints one "GPU N: ..." line per device.
    return any(line.startswith("GPU ") for line in result.stdout.splitlines())


def _expected_sim_frames(recipe: ValidatedRecipe) -> int | None:
    """How many sim_*.ply a complete run should write for this recipe.

    The MPM sim emits ``frame_num + 1`` plys: one initial-state frame
    (sim_0000000000.ply) plus one per simulated step. Empirically confirmed
    against stable production runs (frame_num=30 -> 31 plys, 150 -> 151).

    Returns None when the recipe carries no usable ``frame_num`` so the
    guard falls back to the NaN-drop signature only (never a false positive
    from a missing field).
    """
    raw = recipe.get("frame_num")
    try:
        frame_num = int(raw)
    except (TypeError, ValueError):
        return None
    if frame_num <= 0:
        return None
    return frame_num + 1


def _find_reference_ply(model_dir: Path) -> Path | None:
    """Return the highest-iteration point_cloud.ply under model/point_cloud/.

    Mirrors run_sim.sh's `find ... | sort -V | tail -n 1` so we keep
    bug-for-bug compat with the prior behavior. iteration_30000 wins
    over iteration_7000 (version sort, not lex sort).
    """
    pc_root = model_dir / "point_cloud"
    if not pc_root.is_dir():
        return None
    candidates = list(pc_root.rglob("point_cloud.ply"))
    if not candidates:
        return None

    def _iter_num(p: Path) -> int:
        m = re.search(r"iteration_(\d+)", str(p))
        return int(m.group(1)) if m else -1

    return max(candidates, key=_iter_num)


def _read_pid_starttime(pid: int) -> float | None:
    """Read /proc/<pid>/stat field 22 (starttime in clock ticks).

    Persisted alongside pgid so Phase 4 boot recovery can defend against
    PID reuse (same logic core/state.py:is_pid_alive_with_starttime
    uses on read-back).
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    try:
        rest = raw.rsplit(")", 1)[-1].split()
        return float(rest[19])
    except (IndexError, ValueError):
        return None
    except Exception:
        return None


async def _wait_capturing_stderr(
    proc: asyncio.subprocess.Process,
    sink: list[str],
    log_file=None,
) -> int:
    """Await the process, draining stderr into `sink` line-by-line.

    Returns the process return code. stdout is drained in parallel so
    the pipe never blocks; stderr is retained for the classifier. When
    `log_file` (a text-mode file handle) is supplied, BOTH streams are
    additionally teed to it as they arrive — this is the on-disk
    `run.log` the API serves via /api/runs/{name}/log and that the
    workbench console polls. Without it, the run console stays empty
    for the whole sim (a regression introduced when the legacy
    core/runner.py was deleted in fe2831e and nothing took over the
    persist-stdout-to-run.log responsibility).
    """
    assert proc.stderr is not None
    assert proc.stdout is not None

    def _tee(line: str) -> None:
        if log_file is not None:
            try:
                log_file.write(line)
                log_file.flush()
            except (OSError, ValueError):
                # File closed / disk full: drop the line rather than crash
                # the run. stderr still goes to the classifier; the sim
                # itself keeps running.
                pass

    async def _drain_stderr() -> None:
        async for raw in proc.stderr:
            line = raw.decode(errors="replace")
            sink.append(line)
            _tee(line)

    async def _drain_stdout() -> None:
        async for raw in proc.stdout:
            _tee(raw.decode(errors="replace"))

    await asyncio.gather(_drain_stderr(), _drain_stdout())
    return await proc.wait()
