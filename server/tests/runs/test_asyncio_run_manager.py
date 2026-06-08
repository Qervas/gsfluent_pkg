"""AsyncioRunManager surface tests.

Verifies the RunManager Protocol surface (submit / cancel / status /
recover_on_boot / list_active / wait_for) end-to-end against the
MockSimulationEngine. Real subprocess lifecycle is exercised in
tests/integration/ — those use the SubprocessMockSimulationEngine to
spawn an actual PG and verify signal delivery.
"""
import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager, spawn_in_new_pg
from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import EmitLevelMethods
from gsfluent.protocols.runs import (
    RecoveryReport,
    RunId,
    RunManager,
    RunState,
)
from gsfluent.protocols.sim import ModelRef, SimEnvMissingError, SimResult


class _NullEmitter(EmitLevelMethods):
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


class _StubFuser:
    """Placeholder Fuser — never invoked by AsyncioRunManager today."""
    def fuse_sequence_dir(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _StubCodec:
    """Placeholder CacheCodec — never invoked by AsyncioRunManager today."""
    media_type = "application/octet-stream"
    def encode_sequence_dir(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _StubStorage:
    """Placeholder Storage — never invoked by AsyncioRunManager today."""
    async def put(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _SigtermIgnoringSim:
    def __init__(self, script: Path) -> None:
        self.script = script
        self.proc: asyncio.subprocess.Process | None = None

    async def preflight(self) -> None:
        return None

    async def run(self, recipe, model, output_dir, wall_time_sec, on_event) -> SimResult:
        self.proc = await spawn_in_new_pg([sys.executable, str(self.script)], cwd="/tmp")
        pgid = os.getpgid(self.proc.pid)
        on_event.emit("sim.spawned", pid=self.proc.pid, pgid=pgid, process=self.proc)
        await self.proc.wait()
        return SimResult(frames_dir=output_dir, n_frames=0, duration_sec=0.0)


class _PreflightFailingSim:
    async def preflight(self) -> None:
        raise SimEnvMissingError("sim home missing")

    async def run(self, *args, **kwargs) -> SimResult:  # pragma: no cover
        raise AssertionError("run() must not be called after preflight failure")


def _write_sigterm_ignoring_script(path: Path) -> None:
    path.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "while True:\n"
        "    time.sleep(0.05)\n"
    )


async def _wait_for_proc(sim: _SigtermIgnoringSim) -> asyncio.subprocess.Process:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if sim.proc is not None:
            return sim.proc
        await asyncio.sleep(0.02)
    raise AssertionError("sim subprocess was not spawned")


@pytest.fixture
def state_store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path / "state" / "runs")


@pytest.fixture
def run_mgr(tmp_path: Path, state_store: RunStateStore) -> AsyncioRunManager:
    return AsyncioRunManager(
        sim_engine=MockSimulationEngine(n_frames=1, n_particles=2),
        fuser=_StubFuser(),
        cache_codec=_StubCodec(),
        storage=_StubStorage(),
        obs=_NullEmitter(),
        state_store=state_store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


def test_run_manager_satisfies_protocol(run_mgr: AsyncioRunManager) -> None:
    rm: RunManager = run_mgr
    assert isinstance(rm, RunManager)


@pytest.mark.asyncio
async def test_submit_returns_run_id(run_mgr: AsyncioRunManager, tmp_path: Path) -> None:
    recipe = {
        "_run_name": "smoke",
        "_recipe_source_name": "jelly",
        "_particles": 1000,
        "material": "jelly",
        "_output_dir": str(tmp_path / "out"),
    }
    rid = await run_mgr.submit(
        recipe, model=ModelRef(name="fake", path=tmp_path / "fake_model_dir"),
    )
    assert isinstance(rid, str)
    rec = run_mgr._state_store.read(rid)
    assert rec is not None
    assert rec.state in {RunState.QUEUED, RunState.STARTED, RunState.RUNNING,
                         RunState.COMPLETED}


@pytest.mark.asyncio
async def test_status_returns_snapshot(run_mgr: AsyncioRunManager, tmp_path: Path) -> None:
    recipe = {
        "_run_name": "status_test",
        "_recipe_source_name": "jelly",
        "_particles": 1000,
        "material": "jelly",
        "_output_dir": str(tmp_path / "out"),
    }
    rid = await run_mgr.submit(
        recipe, model=ModelRef(name="fake", path=tmp_path / "fake_model_dir"),
    )
    status = await run_mgr.status(rid)
    assert status.id == rid
    assert status.state in set(RunState)


@pytest.mark.asyncio
async def test_status_unknown_run_raises_keyerror(run_mgr: AsyncioRunManager) -> None:
    with pytest.raises(KeyError):
        await run_mgr.status(RunId("does-not-exist"))


@pytest.mark.asyncio
async def test_preflight_failure_marks_run_failed_and_unblocks_wait_for(
    tmp_path: Path, state_store: RunStateStore,
) -> None:
    mgr = AsyncioRunManager(
        sim_engine=_PreflightFailingSim(),
        fuser=_StubFuser(),
        cache_codec=_StubCodec(),
        storage=_StubStorage(),
        obs=_NullEmitter(),
        state_store=state_store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )
    rid = await mgr.submit(
        {
            "_run_name": "preflight_fail",
            "_particles": 1,
            "_output_dir": str(tmp_path / "out"),
        },
        model=ModelRef(name="fake", path=tmp_path),
    )

    status = await asyncio.wait_for(mgr.wait_for(rid), timeout=1.0)

    assert status.state == RunState.FAILED
    assert status.error == {
        "kind": "sim.env_missing",
        "message": "sim home missing",
    }


@pytest.mark.asyncio
async def test_cancel_is_idempotent_on_unknown_run(run_mgr: AsyncioRunManager) -> None:
    """cancel() on an unknown run is a no-op (idempotent per the Protocol)."""
    # Should not raise.
    await run_mgr.cancel(RunId("never-existed"))


@pytest.mark.asyncio
async def test_cancel_escalates_sigterm_ignoring_subprocess_to_sigkill(
    tmp_path: Path, state_store: RunStateStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GSFLUENT_KILL_GRACE_SEC", "0.2")
    script = tmp_path / "ignore_sigterm.py"
    _write_sigterm_ignoring_script(script)
    sim = _SigtermIgnoringSim(script)
    mgr = AsyncioRunManager(
        sim_engine=sim,
        fuser=_StubFuser(),
        cache_codec=_StubCodec(),
        storage=_StubStorage(),
        obs=_NullEmitter(),
        state_store=state_store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )
    rid = await mgr.submit(
        {"_run_name": "cancel_kill", "_particles": 1, "_output_dir": str(tmp_path / "out")},
        model=ModelRef(name="fake", path=tmp_path),
    )
    proc = await _wait_for_proc(sim)
    await asyncio.sleep(0.2)
    try:
        await mgr.cancel(rid)
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.returncode is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            await proc.wait()


@pytest.mark.asyncio
async def test_wall_time_escalates_sigterm_ignoring_subprocess_to_sigkill(
    tmp_path: Path, state_store: RunStateStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GSFLUENT_KILL_GRACE_SEC", "0.2")
    script = tmp_path / "ignore_sigterm.py"
    _write_sigterm_ignoring_script(script)
    sim = _SigtermIgnoringSim(script)
    mgr = AsyncioRunManager(
        sim_engine=sim,
        fuser=_StubFuser(),
        cache_codec=_StubCodec(),
        storage=_StubStorage(),
        obs=_NullEmitter(),
        state_store=state_store,
        wall_time_cap_sec=1,
        particle_count_cap=500_000,
    )
    rid = await mgr.submit(
        {
            "_run_name": "timeout_kill",
            "_particles": 1,
            "_output_dir": str(tmp_path / "out"),
            "wall_time_sec": 1,
        },
        model=ModelRef(name="fake", path=tmp_path),
    )
    proc = await _wait_for_proc(sim)
    try:
        await mgr.wait_for(rid)
        assert proc.returncode == -signal.SIGKILL
    finally:
        if proc.returncode is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            await proc.wait()


@pytest.mark.asyncio
async def test_recover_on_boot_returns_zero_counts_with_empty_state_dir(
    run_mgr: AsyncioRunManager,
) -> None:
    """With an empty state dir, recover_on_boot returns all zeros."""
    report = await run_mgr.recover_on_boot()
    assert isinstance(report, RecoveryReport)
    assert report.reattached == 0
    assert report.interrupted == 0
    assert report.terminal_already == 0


@pytest.mark.asyncio
async def test_recover_on_boot_marks_orphan_runs_as_interrupted(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    """A state file in QUEUED/STARTED/RUNNING with no matching live PID
    should transition to INTERRUPTED on recovery."""
    from gsfluent.core.state import RunStateRecord
    state_store.write(RunStateRecord(
        id="orphan-1",
        state=RunState.RUNNING,
        pid=2**31 - 1,  # impossible PID
        pid_starttime=1.0,
    ))
    state_store.write(RunStateRecord(
        id="orphan-2",
        state=RunState.QUEUED,
    ))
    state_store.write(RunStateRecord(
        id="done-already",
        state=RunState.COMPLETED,
    ))
    report = await run_mgr.recover_on_boot()
    assert report.interrupted == 2
    assert report.terminal_already == 1
    # State files updated.
    orphan = state_store.read("orphan-1")
    assert orphan is not None
    assert orphan.state == RunState.INTERRUPTED


@pytest.mark.asyncio
async def test_stream_events_returns_empty_iterator_for_unknown_run(
    run_mgr: AsyncioRunManager,
) -> None:
    """stream_events currently returns an empty iterator until a future
    change wires it to a real per-run event channel."""
    events = []
    async for ev in await run_mgr.stream_events(RunId("unknown")):
        events.append(ev)
    assert events == []


@pytest.mark.asyncio
async def test_list_active_filters_terminal_runs(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    """list_active() returns only non-terminal records from the state store."""
    from gsfluent.core.state import RunStateRecord
    state_store.write(RunStateRecord(
        id="active-1",
        state=RunState.RUNNING,
        sequence_name="active_run",
    ))
    state_store.write(RunStateRecord(
        id="active-2",
        state=RunState.QUEUED,
        sequence_name="queued_run",
    ))
    state_store.write(RunStateRecord(
        id="done-1",
        state=RunState.COMPLETED,
        sequence_name="finished_run",
    ))
    active = run_mgr.list_active()
    ids = {s.id for s in active}
    names = {s.sequence_name for s in active}
    assert ids == {"active-1", "active-2"}
    assert names == {"active_run", "queued_run"}
