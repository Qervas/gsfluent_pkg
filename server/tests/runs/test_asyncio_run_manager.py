"""AsyncioRunManager (Phase 2 shim) tests.

Phase 2 makes AsyncioRunManager a thin adapter over the existing
core.runner module-level functions (start_run, cancel_run, list_runs).
Phase 3 will replace the implementation; the Protocol surface stays
stable across that transition.
"""
import asyncio
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateStore
from gsfluent.protocols.runs import (
    RecoveryReport,
    RunId,
    RunManager,
    RunState,
)
from gsfluent.protocols.sim import ModelRef


def _make_fake_sim(path: Path) -> None:
    path.write_text("#!/bin/bash\necho '[fake] running'\nexit 0\n")
    path.chmod(0o755)


# Phase 2 collaborators that the shim accepts but does not yet dispatch through
# (the shim still delegates to core.runner module functions). Phase 3 swaps the
# delegation for direct ownership, at which point these stubs get replaced by
# the real concretes wired in composition.py.
class _NullEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


class _StubSim:
    """Placeholder SimulationEngine — never invoked by the Phase 2 shim."""
    async def run(self, *a, **kw):  # pragma: no cover - shim never calls
        raise NotImplementedError("Phase 3 replaces the shim with direct sim dispatch")


class _StubFuser:
    """Placeholder Fuser — never invoked by the Phase 2 shim."""
    def fuse_sequence_dir(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _StubCodec:
    """Placeholder CacheCodec — never invoked by the Phase 2 shim."""
    media_type = "application/octet-stream"
    def encode_sequence_dir(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


class _StubStorage:
    """Placeholder Storage — never invoked by the Phase 2 shim."""
    async def put(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def state_store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path / "state" / "runs")


@pytest.fixture
def run_mgr(tmp_path: Path, state_store: RunStateStore, monkeypatch) -> AsyncioRunManager:
    # Point the legacy runner at a tmp fused dir + fake sim wrapper so the
    # shim's delegation can be exercised without touching real disk layout.
    fake_sim = tmp_path / "fake_sim.sh"
    _make_fake_sim(fake_sim)
    from gsfluent.core import runner
    monkeypatch.setattr(runner, "SIM_SCRIPT_RUNNER", fake_sim)
    monkeypatch.setattr(runner, "FUSED_DIR", tmp_path / "fused")
    monkeypatch.setattr(runner, "NPZ_REBUILD_AFTER_RUN", False)
    runner._RUNS.clear()
    return AsyncioRunManager(
        sim_engine=_StubSim(),
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
        "_model_dir": str(tmp_path / "fake_model_dir"),
        "_recipe_source_name": "jelly",
        "_particles": 1000,
        "material": "jelly",
    }
    rid = await run_mgr.submit(
        recipe, model=ModelRef(name="fake", path=tmp_path / "fake_model_dir"),
    )
    assert isinstance(rid, str)
    # The shim writes a state record at submit time.
    rec = run_mgr._state_store.read(rid)
    assert rec is not None
    assert rec.state in {RunState.QUEUED, RunState.STARTED, RunState.RUNNING}


@pytest.mark.asyncio
async def test_status_returns_snapshot(run_mgr: AsyncioRunManager, tmp_path: Path) -> None:
    recipe = {
        "_run_name": "status_test",
        "_model_dir": str(tmp_path / "fake_model_dir"),
        "_recipe_source_name": "jelly",
        "_particles": 1000,
        "material": "jelly",
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
async def test_cancel_is_idempotent_on_unknown_run(run_mgr: AsyncioRunManager) -> None:
    """cancel() on an unknown run is a no-op (idempotent per the Protocol)."""
    # Should not raise.
    await run_mgr.cancel(RunId("never-existed"))


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
    """Phase 2 shim returns an empty iterator for stream_events; Phase 3
    will wire it to a real per-run event channel."""
    events = []
    async for ev in await run_mgr.stream_events(RunId("unknown")):
        events.append(ev)
    assert events == []
