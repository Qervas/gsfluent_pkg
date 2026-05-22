"""AsyncioRunManager — RunManager Protocol concrete implementation.

Phase 2 scope: thin adapter over the legacy core.runner module functions.

Phase 4 rewire: when sim_engine is provided, submit() drives it directly
via a background asyncio task that spawns the sim subprocess in its own
process group, persists pid/pgid/pid_starttime to RunStateRecord (so
recover_on_boot can defend against PID reuse), and on completion records
the final state. cancel() uses escalate_kill_pg against the persisted
pgid. When sim_engine is None (legacy callers), submit() falls back to
the original _runner.start_run delegation.

Cross-plan attribute contract: `_state` (RunStateStore), `_obs`
(EventEmitter), `_procs` (RunId -> Process), `_futures` (RunId -> Future),
`_pgids` (RunId -> int), `_reattached` (set[RunId] for boot-found runs).
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
        """Schedule a run.

        Recipe carries reserved underscore-prefixed keys for fields not
        in the Protocol (`_run_name`, `_recipe_source_name`, `_particles`).
        When `self._sim` is a real SimulationEngine, the run is driven by a
        background asyncio task that owns the subprocess lifecycle (Phase 4
        rewire). When `self._sim` is None (legacy callers), falls back to
        the original `_runner.start_run` delegation path.
        """
        run_name = recipe.get("_run_name")
        if not run_name:
            raise ValidationError("recipe missing '_run_name' (shim convention)")
        recipe_source_name = recipe.get("_recipe_source_name", "unknown")
        particles = recipe.get("_particles", 0)
        try:
            particles = int(particles)
        except (TypeError, ValueError):
            raise ValidationError(f"recipe '_particles' must be int; got {particles!r}")
        if particles < 0:
            raise CapExceededError(f"particles must be >= 0; got {particles}")

        # --- Phase 4 rewire path: drive sim_engine directly when present ---
        if self._sim is not None:
            return await self._submit_via_engine(
                recipe=recipe,
                model=model,
                run_name=run_name,
                particles=particles,
            )

        # --- Legacy fallback: delegate to runner.start_run (Phase 2 shim) ---
        legacy_run_id = await _runner.start_run(
            run_name=run_name,
            model_dir=model.path,
            recipe_data=recipe,
            recipe_source_name=recipe_source_name,
            particles=particles,
        )
        rid = RunId(legacy_run_id)
        self._state_store.write(RunStateRecord(
            id=rid,
            state=RunState.RUNNING,
            sequence_name=run_name,
        ))
        return rid

    async def _submit_via_engine(
        self,
        *,
        recipe: ValidatedRecipe,
        model: ModelRef,
        run_name: str,
        particles: int,
    ) -> RunId:
        """Drive self._sim directly. Spawns a background task that:
          1. Calls self._sim.run(recipe, model, output_dir, wall_time, on_event)
          2. Intercepts the engine's sim.spawned event to persist
             pid/pgid/pid_starttime to RunStateRecord (so recover_on_boot
             can defend against PID reuse on restart).
          3. On completion, persists final state (COMPLETED / FAILED /
             CANCELLED with classified error).
        Returns the run_id immediately; the task runs in the background.
        """
        import uuid
        rid = RunId(uuid.uuid4().hex[:12])

        # Persist initial QUEUED record BEFORE spawning so a crash between
        # write() and create_task() leaves a discoverable record that
        # recover_on_boot can mark INTERRUPTED.
        self._state_store.write(RunStateRecord(
            id=rid,
            state=RunState.QUEUED,
            sequence_name=run_name,
        ))

        # Per-run logger: bind run_id + sequence_name once so every
        # downstream emit() in this run's scope auto-attaches that context.
        # (Phase 6: required by the structured-observability invariant.)
        run_obs = self._obs.child(run_id=rid, sequence_name=run_name)
        run_obs.emit(
            "run.queued",
            particle_count=particles,
            wall_time_cap=self._wall_time_cap_sec,
        )

        # Background task drives the engine end-to-end. Holding the Future
        # in self._futures lets wait_for() block on it from tests + ops.
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        self._futures[rid] = fut

        task = asyncio.create_task(
            self._run_to_completion(rid, recipe, model, run_name, fut, run_obs)
        )
        self._tasks[rid] = task
        return rid

    async def _run_to_completion(
        self,
        run_id: RunId,
        recipe: ValidatedRecipe,
        model: ModelRef,
        run_name: str,
        fut: asyncio.Future[None],
        run_obs: EventEmitter,
    ) -> None:
        """Background task: drive sim_engine.run() through to terminal state.

        `run_obs` is the per-run emitter built by submit() (already bound
        to run_id + sequence_name via obs.child()). Every event emitted in
        this run's scope flows through it so journalctl + the SSE feed see
        the same context on every line.
        """
        # Wall-time cap: prefer recipe-supplied value, clamp to configured cap.
        recipe_wall = int(recipe.get("wall_time_sec", self._wall_time_cap_sec))
        wall_time = min(recipe_wall, self._wall_time_cap_sec)

        # Output dir convention - mirrors what MPMSimulationEngine expects.
        output_dir = Path(recipe.get("_output_dir") or f"/tmp/gsfluent-{run_id}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Spying EventEmitter: intercepts sim.spawned to capture pid/pgid/
        # pid_starttime and persist them onto the RunStateRecord. Wraps
        # run_obs (NOT self._obs) so the sim.* events also carry the bound
        # run_id + sequence_name context. Also tracks whether the engine
        # already emitted an error.* event so the unified except block
        # below avoids double-emitting (spec invariant: one event per
        # error at its boundary).
        mgr = self
        engine_error_emitted: set[str] = set()

        class _PidCapturingEmitter:
            def __init__(self, inner):
                self._inner = inner

            def emit(self, event: str, **context):
                self._inner.emit(event, **context)
                if event.startswith("error."):
                    engine_error_emitted.add(event)
                if event == "sim.spawned":
                    pid = context.get("pid")
                    pgid = context.get("pgid")
                    pid_starttime = context.get("pid_starttime")
                    if pid is not None:
                        mgr._pgids[run_id] = pgid if pgid is not None else pid
                        rec = mgr._state_store.read(run_id)
                        if rec is not None:
                            mgr._state_store.write(rec.transition(
                                state=RunState.RUNNING,
                                pid=int(pid),
                                pgid=int(pgid) if pgid is not None else None,
                                pid_starttime=float(pid_starttime) if pid_starttime is not None else None,
                                started_at=time.time(),
                            ))

            def child(self, **context):
                if hasattr(self._inner, "child"):
                    return _PidCapturingEmitter(self._inner.child(**context))
                return self

        spying = _PidCapturingEmitter(run_obs)

        # Preflight: catch env-missing errors early so the lifecycle event
        # chain reflects whether the engine ever got to run. preflight() is
        # best-effort here — engines that defer all checks to run() can
        # no-op it.
        try:
            await self._sim.preflight()
            run_obs.emit("run.preflight_ok")
        except Exception:
            # Re-raise so the unified except below records the failure with
            # the right error.kind mapping.
            raise

        # Mark STARTED before the engine fires up.
        rec = self._state_store.read(run_id)
        if rec is not None:
            self._state_store.write(rec.transition(state=RunState.STARTED))
        started_at = time.time()
        run_obs.emit("run.started", started_at=started_at)

        try:
            # Wall-time cap: on timeout we issue escalate_kill_pg against the
            # captured pgid (if any). The engine's run() returns a SimResult
            # on success or raises a typed SimError on failure.
            def _on_timeout() -> None:
                pgid = mgr._pgids.get(run_id)
                if pgid is None:
                    return
                # Best-effort: ask the kernel to terminate the group; the
                # actual escalation ladder is owned by the engine when it
                # uses spawn_in_new_pg / escalate_kill_pg. Here we just
                # send a polite SIGTERM as a baseline.
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

            async def _engine_run():
                return await self._sim.run(
                    recipe, model, output_dir, wall_time, spying,
                )

            result = await run_with_wall_time(
                coro_factory=_engine_run,
                wall_time_sec=wall_time,
                on_timeout=_on_timeout,
            )
            # Lifecycle: sim done -> emit run.simmed. (Fuse + pack stages
            # historically lived in the engine itself; if a future engine
            # decomposes them back into the run manager those stages would
            # also emit run.fused / run.packed here.)
            run_obs.emit(
                "run.simmed",
                n_frames=result.n_frames,
                duration_sec=result.duration_sec,
            )

            # Success path.
            rec = self._state_store.read(run_id)
            if rec is not None:
                self._state_store.write(rec.transition(
                    state=RunState.COMPLETED,
                    finished_at=time.time(),
                    paths={"frames_dir": str(result.frames_dir)},
                ))
            run_obs.emit(
                "run.completed",
                n_frames=result.n_frames,
                duration_sec=result.duration_sec,
            )
            if not fut.done():
                fut.set_result(None)
        except SimWallTimeExceededError as e:
            rec = self._state_store.read(run_id)
            if rec is not None:
                self._state_store.write(rec.transition(
                    state=RunState.FAILED,
                    finished_at=time.time(),
                    error={"kind": "sim.wall_time_exceeded", "message": str(e)},
                ))
            run_obs.emit(
                "error.sim.wall_time_exceeded",
                wall_time_sec=wall_time,
                message=str(e),
            )
            run_obs.emit("run.failed", kind="sim.wall_time_exceeded")
            if not fut.done():
                fut.set_result(None)
        except asyncio.CancelledError:
            # Cooperative cancellation - cancel() above set the user intent.
            # Mark CANCELLED on disk and re-raise so the task records cancel.
            rec = self._state_store.read(run_id)
            if rec is not None and not rec.is_terminal():
                self._state_store.write(rec.transition(
                    state=RunState.CANCELLED,
                    finished_at=time.time(),
                ))
            run_obs.emit("run.cancelled")
            if not fut.done():
                fut.set_result(None)
            raise
        except Exception as e:
            # Typed SimError or any other failure - record and emit.
            kind = type(e).__name__
            # Map common SimError subclasses to spec error.kind strings.
            from gsfluent.protocols.sim import (
                GPUUnavailableError, SimCrashedError, SimEnvMissingError,
                SimGpuOomError, SimInterpreterMissingError, SimUnstableRecipeError,
            )
            kind_map = {
                SimGpuOomError: "sim.gpu_oom",
                SimUnstableRecipeError: "sim.unstable_recipe",
                SimCrashedError: "sim.crashed",
                SimEnvMissingError: "sim.env_missing",
                SimInterpreterMissingError: "sim.interpreter_missing",
                GPUUnavailableError: "sim.gpu_unavailable",
            }
            mapped_kind = next(
                (v for k, v in kind_map.items() if isinstance(e, k)),
                f"internal.{kind.lower()}",
            )
            rec = self._state_store.read(run_id)
            if rec is not None:
                self._state_store.write(rec.transition(
                    state=RunState.FAILED,
                    finished_at=time.time(),
                    error={"kind": mapped_kind, "message": str(e)},
                ))
            # Boundary event: one structured emit per error per spec invariant.
            # MPMSimulationEngine already emits its own classified
            # error.sim.* events from inside run(); only mirror an
            # error.* event from the run manager when the engine did NOT
            # have a chance to do so itself (typically preflight() and
            # internal failures).
            internal_kind = f"internal.{kind.lower()}"
            if mapped_kind == internal_kind:
                run_obs.emit(
                    "error.internal",
                    where="run_to_completion",
                    error_type=kind,
                    message=str(e),
                )
            elif mapped_kind in {
                "sim.env_missing",
                "sim.interpreter_missing",
                "sim.gpu_unavailable",
            }:
                # Preflight-class errors: the engine raised before its own
                # error.sim.* emission path, so the run manager mirrors it.
                run_obs.emit(
                    f"error.{mapped_kind}",
                    message=str(e),
                )
            else:
                # sim.gpu_oom / sim.unstable_recipe / sim.crashed: the
                # MPM engine already emits the matching error.sim.* event
                # from inside run() before raising. Spec invariant
                # requires exactly one error event per failure: only
                # mirror here when the engine did not (typical for test
                # mocks that raise a typed SimError without emitting,
                # see _OomSim in the taxonomy test).
                expected_event = f"error.{mapped_kind}"
                if expected_event not in engine_error_emitted:
                    run_obs.emit(
                        expected_event,
                        message=str(e),
                    )
            run_obs.emit("run.failed", kind=mapped_kind, error=str(e))
            if not fut.done():
                fut.set_result(None)
        finally:
            # Clean up internal handles regardless of outcome.
            self._tasks.pop(run_id, None)
            self._pgids.pop(run_id, None)

    async def cancel(self, run_id: RunId) -> None:
        """Idempotent cancellation.

        Phase 4 rewire: when self._sim drives the run (modern path), use
        escalate_kill_pg against the captured pgid + cancel the supervising
        asyncio task. Falls back to _runner.cancel_run for runs that came
        in via the legacy path.
        """
        rec = self._state_store.read(run_id)
        if rec is None or rec.is_terminal():
            # Idempotent: unknown / already-done runs return silently.
            return

        # Mark CANCELLING before tearing things down so a crash during
        # cancel still leaves a discoverable in-flight state.
        self._state_store.write(rec.transition(state=RunState.CANCELLING))
        # Bind sequence_name so the emit matches the per-run logger contract
        # other lifecycle events use.
        cancel_obs = self._obs.child(
            run_id=run_id,
            sequence_name=rec.sequence_name,
        ) if hasattr(self._obs, "child") else self._obs
        cancel_obs.emit("run.cancelling")

        # Modern path: we own the task + pgid -> escalate signal ladder.
        task = self._tasks.get(run_id)
        proc = self._procs.get(run_id)
        pgid = self._pgids.get(run_id)

        if task is not None or proc is not None or pgid is not None:
            # If we have a live Process handle, use escalate_kill_pg for the
            # full SIGTERM->grace->SIGKILL ladder. Otherwise fall back to a
            # direct killpg call against the captured pgid.
            if proc is not None and pgid is not None:
                try:
                    await escalate_kill_pg(proc, pgid=pgid)
                except Exception:
                    # Best-effort - the engine might have torn down already.
                    pass
            elif pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            # Cancel the supervising task so _run_to_completion records
            # CANCELLED on disk.
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            return

        # Legacy path: delegate to runner.cancel_run for runs spawned
        # through the Phase 2 shim path.
        _runner.cancel_run(run_id)

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
