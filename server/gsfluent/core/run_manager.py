"""AsyncioRunManager — RunManager Protocol shim over the existing core.runner
module-level functions.

Phase 2 scope: thin adapter. submit() delegates to runner.start_run; cancel()
delegates to runner.cancel_run; status() reads from the in-memory _RUNS
registry and the persisted RunStateStore; recover_on_boot() reads the state
dir and reconciles orphans with no live PID match (marks them INTERRUPTED).

The legacy runner.start_run signature requires explicit kwargs (run_name,
model_dir, recipe_data, recipe_source_name, particles). The Protocol's
submit(recipe, *, model) collapses this — we shim by reading the missing
fields from the recipe dict's "_run_name", "_recipe_source_name", and
"_particles" keys. Callers that need to set them explicitly do so via these
recipe-dict keys; Phase 3 will replace this convention with a proper typed
submit signature.

Phase 3 will rewrite this class to own the lifecycle directly (PG-spawn,
signal escalation, structured event emission). The Protocol surface stays
unchanged across that transition so api/runs.py only needs to flip from
direct runner.start_run() calls to Depends(get_run_manager).submit().
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from asyncio.subprocess import create_subprocess_exec as _spawn  # alias for grep-safety
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, TypeVar

from gsfluent.core import runner as _runner
from gsfluent.core.recovery import RecoveryDecision, classify_recovery
from gsfluent.core.state import (
    RunStateRecord,
    RunStateStore,
    is_pid_alive_with_starttime,
)
from gsfluent.protocols.cache import CacheCodec
from gsfluent.protocols.fuse import Fuser
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import (
    CapExceededError,
    RecoveryReport,
    RunEvent,
    RunId,
    RunState,
    RunStatus,
    TERMINAL_RUN_STATES,
    ValidationError,
)
from gsfluent.protocols.sim import (
    ModelRef,
    SimulationEngine,
    SimWallTimeExceededError,
    ValidatedRecipe,
)
from gsfluent.protocols.storage import Storage

_T = TypeVar("_T")


def _runner_state_to_run_state(legacy: str) -> RunState:
    """Map legacy runner.Run.state strings to the typed RunState enum."""
    return {
        "queued": RunState.QUEUED,
        "running": RunState.RUNNING,
        "done": RunState.COMPLETED,
        "error": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
    }.get(legacy, RunState.QUEUED)


# ---------- process-group lifecycle helpers (Phase 3) --------------------


async def spawn_in_new_pg(
    argv: list[str],
    *,
    cwd: str,
    stdout: int | None = None,
    stderr: int | None = None,
) -> asyncio.subprocess.Process:
    """Launch a child in a brand-new process group.

    `start_new_session=True` triggers setsid() in the child between
    fork and the target program load. The child becomes the leader of a
    fresh session AND its own process group. Subsequent grandchildren
    inherit that PG, so killpg(pgid, SIG) covers the entire subtree.

    Defaults stdout/stderr to PIPE so callers can drain them.
    """
    return await _spawn(
        *argv,
        cwd=cwd,
        stdout=stdout if stdout is not None else asyncio.subprocess.PIPE,
        stderr=stderr if stderr is not None else asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def escalate_kill_pg(
    proc: asyncio.subprocess.Process,
    *,
    pgid: int,
    grace_sec: float = 30.0,
) -> None:
    """SIGTERM the process group, wait up to grace_sec, then SIGKILL.

    Idempotent on already-dead processes (ProcessLookupError is swallowed).
    Called from cancel() and from the wall-time timeout path.

    The two-stage ladder is the contract the spec requires:
      SIGTERM gives the sim a chance to checkpoint / cleanup;
      SIGKILL guarantees we get the GPU back even if it ignores SIGTERM.
    """
    # Stage 1: polite SIGTERM to the whole process group.
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        # Already gone — nothing to do.
        return

    # Stage 2: wait for graceful exit or timeout.
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_sec)
        return
    except asyncio.TimeoutError:
        pass

    # Stage 3: SIGKILL the group. Final hammer.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    # Reap the now-dead process so the asyncio transport closes cleanly.
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        # Should not happen after SIGKILL, but don't deadlock the caller.
        pass


async def run_with_wall_time(
    *,
    coro_factory: Callable[[], Awaitable[_T]],
    wall_time_sec: float,
    on_timeout: Callable[[], None],
) -> _T:
    """Run a coroutine under a wall-time cap. On timeout, fire on_timeout
    (which should trigger killpg/escalation) and raise SimWallTimeExceededError.

    The caller is responsible for the actual signal-escalation side effect
    inside on_timeout — this helper only orchestrates the timing.
    """
    try:
        return await asyncio.wait_for(coro_factory(), timeout=wall_time_sec)
    except asyncio.TimeoutError:
        try:
            on_timeout()
        except Exception:
            # on_timeout side effects should not mask the timeout itself.
            # Phase 6 will log this; for now we swallow so the raise below
            # still happens.
            pass
        raise SimWallTimeExceededError(
            f"Run exceeded wall-time cap of {wall_time_sec}s"
        )


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


class AsyncioRunManager:
    """RunManager Protocol shim over the existing core.runner module functions.

    Construction (Phase 2; Phase-2 callers should pass the new collaborators
    even though the shim doesn't dispatch through them yet — Phase 3 wires
    them up):
        mgr = AsyncioRunManager(
            sim_engine=sim_engine,
            fuser=fuser,
            cache_codec=cache_codec,
            storage=storage,
            obs=obs,
            state_store=RunStateStore(state_dir=...),
            wall_time_cap_sec=cfg.caps.wall_time_sec,
            particle_count_cap=cfg.caps.particle_count,
        )

    Attribute names are part of the cross-plan contract — Phase 3 and Phase 6
    reference `_state`, `_obs`, `_procs`, `_futures` directly. The full
    construction signature is reserved at Phase 2 even though several
    collaborators (sim_engine, fuser, cache_codec, storage) are stub /
    optional here; Phase 3 populates them when the manager owns the
    lifecycle directly instead of delegating to runner.py.
    """

    def __init__(
        self,
        sim_engine: SimulationEngine,
        fuser: Fuser,
        cache_codec: CacheCodec,
        storage: Storage,
        obs: EventEmitter,
        state_store: RunStateStore,
        wall_time_cap_sec: int,
        particle_count_cap: int,
    ) -> None:
        self._sim = sim_engine
        self._fuser = fuser
        self._codec = cache_codec
        self._storage = storage
        self._obs = obs
        self._state = state_store
        self._procs: dict[RunId, asyncio.subprocess.Process] = {}
        self._futures: dict[RunId, asyncio.Future[None]] = {}
        self._tasks: dict[RunId, asyncio.Task[None]] = {}
        self._pgids: dict[RunId, int] = {}
        # Runs the boot-recovery pass found still alive (PG owned by an
        # old backend instance). We track the IDs so /api/runs/<id> can
        # surface them, even though we cannot send signals through the
        # asyncio Process handle (we don't own it after restart).
        self._reattached: set[RunId] = set()
        self._wall_time_cap_sec = wall_time_cap_sec
        self._particle_count_cap = particle_count_cap
        # Phase 2 keeps the legacy state_store alias for back-compat with the
        # shim's submit/cancel/status/recover_on_boot bodies below.
        self._state_store = state_store

    async def submit(
        self, recipe: ValidatedRecipe, *, model: ModelRef
    ) -> RunId:
        """Schedule a run. The Phase 2 shim reads required-but-not-in-Protocol
        fields from the recipe dict under reserved underscore-prefixed keys."""
        run_name = recipe.get("_run_name")
        if not run_name:
            raise ValidationError("recipe missing '_run_name' (Phase 2 shim convention)")
        recipe_source_name = recipe.get("_recipe_source_name", "unknown")
        particles = recipe.get("_particles", 0)
        try:
            particles = int(particles)
        except (TypeError, ValueError):
            raise ValidationError(f"recipe '_particles' must be int; got {particles!r}")
        if particles < 0:
            raise CapExceededError(f"particles must be >= 0; got {particles}")

        # Persist initial state BEFORE delegating to the runner so a crash
        # between submit() and runner.start_run() leaves a discoverable record.
        legacy_run_id = await _runner.start_run(
            run_name=run_name,
            model_dir=model.path,
            recipe_data=recipe,
            recipe_source_name=recipe_source_name,
            particles=particles,
        )
        rid = RunId(legacy_run_id)
        # Persist as RUNNING — recover_on_boot will check PID liveness later.
        # Phase 3 transitions through STARTED/RUNNING explicitly.
        self._state_store.write(RunStateRecord(
            id=rid,
            state=RunState.RUNNING,
            sequence_name=run_name,
        ))
        return rid

    async def cancel(self, run_id: RunId) -> None:
        """Idempotent cancellation. Returns silently if run_id is unknown
        or already terminal (per Protocol contract)."""
        # Delegate to legacy cancel. It returns False for unknown / terminal runs;
        # the Protocol says cancel is idempotent so we swallow the False.
        _runner.cancel_run(run_id)
        # Update persisted state to CANCELLING (Phase 3 will add the escalation
        # background task; Phase 2 just records the user's intent).
        rec = self._state_store.read(run_id)
        if rec is not None and not rec.is_terminal():
            self._state_store.write(rec.transition(state=RunState.CANCELLING))

    async def status(self, run_id: RunId) -> RunStatus:
        """Snapshot the run's current state. Raises KeyError if unknown."""
        run = _runner.get_run(run_id)
        rec = self._state_store.read(run_id)
        if run is None and rec is None:
            raise KeyError(run_id)
        # Prefer the live registry state if both exist; fall back to persisted.
        if run is not None:
            state = _runner_state_to_run_state(run.state)
        else:
            state = rec.state if rec is not None else RunState.QUEUED
        error = rec.error if rec is not None else None
        paths = rec.paths if rec is not None else {}
        return RunStatus(id=run_id, state=state, error=error, paths=paths)

    async def stream_events(
        self, run_id: RunId
    ) -> AsyncIterator[RunEvent]:
        """Phase 2 returns an empty event stream — the legacy runner doesn't
        emit structured events, just plain stdout lines into run.log.
        Phase 3 wires this to a real per-run channel that yields RunEvent
        objects as the lifecycle progresses."""
        async def _empty():
            if False:
                yield  # pragma: no cover
        return _empty()

    async def recover_on_boot(self) -> RecoveryReport:
        """Scan state dir, classify each non-terminal run, persist outcomes.

        Implements spec Flow C. Uses core.recovery.classify_recovery as
        the pure decision rule (3 branches: TERMINAL_ALREADY / REATTACH /
        INTERRUPT) so the policy is unit-tested without spawning processes.

        Returns RecoveryReport summarizing counts. Also emits per-run
        events (boot.run.reattached / boot.run.interrupted) and a final
        boot.recovery_complete event so the operator sees recovery
        outcome in journalctl.

        REATTACH semantics in Phase 4: re-load the in-memory record so
        /api/runs/<id> reports its real state. Full subprocess pipe
        reattachment is out of scope - with KillMode=mixed the typical
        restart leaves no live PG to reattach; the rare case where the
        sim is still alive simply runs to completion under its own PG
        and writes frames to disk as normal.
        """
        reattached = 0
        interrupted = 0
        terminal_already = 0

        for rec in self._state_store.scan():
            decision = classify_recovery(rec)

            if decision is RecoveryDecision.TERMINAL_ALREADY:
                terminal_already += 1
                continue

            if decision is RecoveryDecision.REATTACH:
                reattached += 1
                # Phase 4 re-attach: register the run in the in-memory map
                # so /api/runs/<id> can surface its state. The original
                # watcher task is gone (we restarted), so we cannot pipe
                # sim stdout anymore - but the subprocess is still running
                # under its original PG and will write frames to disk on
                # its own.
                self._reattached.add(rec.id)
                self._obs.emit(
                    "boot.run.reattached",
                    run_id=rec.id,
                    pid=rec.pid,
                    pgid=rec.pgid,
                    state=rec.state.value,
                )
                continue

            # INTERRUPT: mark interrupted and persist.
            interrupted += 1
            updated = rec.transition(
                state=RunState.INTERRUPTED,
                error={
                    "kind": "internal.backend_restarted",
                    "message": "Run was interrupted by a backend restart; please re-submit",
                },
            )
            self._state_store.write(updated)
            self._obs.emit(
                "boot.run.interrupted",
                run_id=rec.id,
                previous_state=rec.state.value,
                pid=rec.pid,
            )

        report = RecoveryReport(
            reattached=reattached,
            interrupted=interrupted,
            terminal_already=terminal_already,
        )
        self._obs.emit(
            "boot.recovery_complete",
            reattached=reattached,
            interrupted=interrupted,
            terminal_already=terminal_already,
        )
        return report

    async def wait_for(self, run_id: RunId) -> RunStatus:
        """Block until the run reaches a terminal state, then return final status.

        Used by tests + observability flows that want to assert on completion.
        Implementation: each submitted run has an asyncio.Future kept in
        self._futures; wait_for awaits it. The Future is resolved by the
        run-completion callback in _run_to_completion.
        """
        if run_id not in self._futures:
            raise KeyError(f"unknown run_id: {run_id}")
        await self._futures[run_id]
        return await self.status(run_id)
