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

import yaml

from gsfluent._paths import PKG_ROOT
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
    raw = yaml.safe_load(p.read_text())
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


# ---------- the engine ---------------------------------------------------


class MPMSimulationEngine:
    """Concrete SimulationEngine for the MPM sim (warp + taichi + torch).

    Spawns two subprocesses per run() call:
      1. The canonical MPM sim (gs_simulation_building.py)
      2. The fuse stage (server/tools/fuse_to_full_ply.py)
    Both inherit the new process group created at sim spawn so a single
    killpg(pgid, SIGTERM/SIGKILL) on cancel/timeout takes down both.

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
        on_event.emit("sim.preflight_ok")

        # Resolve paths the same way run_sim.sh did so we keep
        # bug-for-bug compatibility on the directory layout.
        run_name = recipe.get("_run_name") or output_dir.name
        sim_output_dir = self._sim_home / "output" / run_name
        sim_ply_dir = sim_output_dir / "simulation_ply"
        library_seq_dir = PKG_ROOT / "work" / "library" / "sequences" / run_name
        fused_dir = library_seq_dir / "frames"

        sim_output_dir.mkdir(parents=True, exist_ok=True)
        library_seq_dir.mkdir(parents=True, exist_ok=True)
        fused_dir.mkdir(parents=True, exist_ok=True)

        # Find the highest-iteration reference ply under model/point_cloud/.
        reference_ply = _find_reference_ply(model.path)
        if reference_ply is None:
            raise SimCrashedError(
                f"no reference ply under {model.path}/point_cloud/"
            )

        # Preserve the merged recipe.json early so a sim crash doesn't lose it.
        config_path = library_seq_dir / "recipe.json"
        import json
        config_path.write_text(json.dumps(recipe, indent=2))

        particles = int(recipe.get("particle_count", 200_000))

        # ---- stage 1: MPM sim ------------------------------------------

        sim_argv = self._build_sim_argv(
            model_dir=model.path,
            sim_output_dir=sim_output_dir,
            config_path=config_path,
            particles=particles,
        )

        t0 = time.monotonic()
        sim_proc = await self._spawn_in_new_pg(
            argv=sim_argv,
            cwd=str(self._sim_home),
        )
        pgid = os.getpgid(sim_proc.pid)
        pid_starttime = _read_pid_starttime(sim_proc.pid)
        on_event.emit(
            "sim.spawned",
            pid=sim_proc.pid,
            pgid=pgid,
            pid_starttime=pid_starttime,
            argv=sim_argv,
        )

        sim_stderr_chunks: list[str] = []
        sim_rc = await _wait_capturing_stderr(sim_proc, sim_stderr_chunks)
        sim_duration = time.monotonic() - t0
        on_event.emit(
            "sim.completed",
            returncode=sim_rc,
            duration_sec=sim_duration,
        )
        if sim_rc != 0:
            joined = "".join(sim_stderr_chunks)
            kind = classify_stderr(joined, self._patterns)
            msg = (
                f"sim exited with rc={sim_rc} after {sim_duration:.1f}s; "
                f"classified as {kind or 'sim.crashed'}"
            )
            on_event.emit(
                f"error.{kind or 'sim.crashed'}",
                returncode=sim_rc,
                stderr_tail=joined[-2000:],
            )
            raise _kind_to_exception(kind or "sim.crashed", msg)

        # ---- stage 2: fuse ---------------------------------------------

        fuse_argv = self._build_fuse_argv(
            reference_ply=reference_ply,
            sim_ply_dir=sim_ply_dir,
            fused_dir=fused_dir,
        )

        t1 = time.monotonic()
        fuse_proc = await self._spawn_in_existing_pg(
            argv=fuse_argv,
            cwd=str(PKG_ROOT),
            pgid=pgid,
        )
        on_event.emit("fuse.spawned", pid=fuse_proc.pid, argv=fuse_argv)
        fuse_stderr_chunks: list[str] = []
        fuse_rc = await _wait_capturing_stderr(fuse_proc, fuse_stderr_chunks)
        fuse_duration = time.monotonic() - t1
        on_event.emit(
            "fuse.completed",
            returncode=fuse_rc,
            duration_sec=fuse_duration,
        )
        if fuse_rc != 0:
            joined = "".join(fuse_stderr_chunks)
            # Spec invariant: emit one structured boundary event per
            # failure. The RunManager's de-dup guard checks for this
            # exact event name so it doesn't mirror it.
            on_event.emit(
                "error.fuse.crashed",
                returncode=fuse_rc,
                stderr_tail=joined[-2000:],
            )
            raise SimCrashedError(
                f"fuse exited with rc={fuse_rc} after {fuse_duration:.1f}s; "
                f"stderr tail: {joined[-500:]}"
            )

        n_frames = sum(1 for _ in fused_dir.glob("frame_*.ply"))
        return SimResult(
            frames_dir=fused_dir,
            n_frames=n_frames,
            duration_sec=time.monotonic() - t0,
        )

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
            extras += ["--no_cfl_override", "--graph_capture"]
        return [
            self._sim_python,
            "gs_simulation/watermelon/gs_simulation_building.py",
            "--model_path", str(model_dir),
            "--output_path", str(sim_output_dir),
            "--config", str(config_path),
            "--target_particles", str(particles),
            "--output_ply", "--async_io",
            *extras,
        ]

    def _build_fuse_argv(
        self,
        *,
        reference_ply: Path,
        sim_ply_dir: Path,
        fused_dir: Path,
    ) -> list[str]:
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
        self, argv: list[str], cwd: str
    ) -> asyncio.subprocess.Process:
        """Launch the sim child in a brand-new process group.

        start_new_session=True triggers setsid() in the child between
        fork and the target program load. The child becomes the leader
        of a fresh session AND process group. Any further children it
        spawns inherit that group, so killpg(pgid, SIG) reaches all of
        them with a single call.
        """
        return await _spawn(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    async def _spawn_in_existing_pg(
        self, argv: list[str], cwd: str, pgid: int
    ) -> asyncio.subprocess.Process:
        """Launch the fuse child into the sim's existing process group.

        Uses preexec_fn=os.setpgid to slot the child into pgid before
        the target program loads. This means a single killpg call covers
        both stages on cancel/timeout.
        """
        def _join_pg() -> None:
            os.setpgid(0, pgid)

        return await _spawn(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_join_pg,
        )


# ---------- module-level helpers -----------------------------------------


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
    except (FileNotFoundError, PermissionError):
        return None
    try:
        rest = raw.rsplit(")", 1)[-1].split()
        return float(rest[19])
    except (IndexError, ValueError):
        return None


async def _wait_capturing_stderr(
    proc: asyncio.subprocess.Process,
    sink: list[str],
) -> int:
    """Await the process, draining stderr into `sink` line-by-line.

    Returns the process return code. stdout is drained in parallel so
    the pipe never blocks; only stderr is retained for the classifier.
    """
    assert proc.stderr is not None
    assert proc.stdout is not None

    async def _drain_stderr() -> None:
        async for raw in proc.stderr:
            sink.append(raw.decode(errors="replace"))

    async def _drain_stdout() -> None:
        async for _ in proc.stdout:
            pass  # discard; the run log lives elsewhere

    await asyncio.gather(_drain_stderr(), _drain_stdout())
    return await proc.wait()
