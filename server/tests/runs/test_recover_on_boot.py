"""Tests for AsyncioRunManager.recover_on_boot().

Uses a real RunStateStore on tmp_path; injects fake PIDs to exercise
all three classification branches without spawning subprocesses.
"""
import io
import os
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateRecord, RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.runs import RecoveryReport, RunState


def _read_own_starttime() -> float:
    with open(f"/proc/{os.getpid()}/stat") as f:
        raw = f.read()
    rest = raw.rsplit(")", 1)[-1].split()
    return float(rest[19])


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_state" / "runs"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def state_store(state_dir: Path) -> RunStateStore:
    return RunStateStore(state_dir=state_dir)


@pytest.fixture
def emitter() -> StdlibJSONEmitter:
    return StdlibJSONEmitter(stream=io.StringIO())


@pytest.fixture
def run_mgr(state_store: RunStateStore, emitter: StdlibJSONEmitter) -> AsyncioRunManager:
    """Build an AsyncioRunManager. recover_on_boot does not call sim
    /fuse/codec/storage so None placeholders are fine for the boot-recovery
    code path."""
    return AsyncioRunManager(
        sim_engine=None,
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=emitter,
        state_store=state_store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


@pytest.mark.asyncio
async def test_recover_empty_state_dir(run_mgr: AsyncioRunManager) -> None:
    report = await run_mgr.recover_on_boot()
    assert report == RecoveryReport(reattached=0, interrupted=0, terminal_already=0)


@pytest.mark.asyncio
async def test_recover_counts_terminal_records(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    for i, s in enumerate(
        [RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED]
    ):
        state_store.write(RunStateRecord(id=f"r{i}", state=s))
    report = await run_mgr.recover_on_boot()
    assert report.terminal_already == 4
    assert report.reattached == 0
    assert report.interrupted == 0


@pytest.mark.asyncio
async def test_recover_marks_dead_pid_as_interrupted(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    state_store.write(RunStateRecord(
        id="r-dead",
        state=RunState.RUNNING,
        pid=2**31 - 1,
        pgid=2**31 - 1,
        pid_starttime=1.0,
    ))
    report = await run_mgr.recover_on_boot()
    assert report.interrupted == 1
    # Record on disk is now interrupted with the right error kind.
    loaded = state_store.read("r-dead")
    assert loaded is not None
    assert loaded.state == RunState.INTERRUPTED
    assert loaded.error == {
        "kind": "internal.backend_restarted",
        "message": "Run was interrupted by a backend restart; please re-submit",
    }


@pytest.mark.asyncio
async def test_recover_reattaches_live_pid(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    """Use our own pid + real starttime to simulate a still-running sim."""
    state_store.write(RunStateRecord(
        id="r-alive",
        state=RunState.RUNNING,
        pid=os.getpid(),
        pgid=os.getpid(),
        pid_starttime=_read_own_starttime(),
    ))
    report = await run_mgr.recover_on_boot()
    assert report.reattached == 1
    # Record on disk is unchanged (still RUNNING).
    loaded = state_store.read("r-alive")
    assert loaded is not None
    assert loaded.state == RunState.RUNNING


@pytest.mark.asyncio
async def test_recover_handles_mixed_records(
    run_mgr: AsyncioRunManager, state_store: RunStateStore,
) -> None:
    # 2 terminal, 2 interrupted (dead pid), 1 reattached
    state_store.write(RunStateRecord(id="t0", state=RunState.COMPLETED))
    state_store.write(RunStateRecord(id="t1", state=RunState.FAILED))
    state_store.write(RunStateRecord(id="d0", state=RunState.RUNNING,
                                pid=2**31 - 1, pgid=2**31 - 1, pid_starttime=1.0))
    state_store.write(RunStateRecord(id="d1", state=RunState.STARTED,
                                pid=2**31 - 1, pgid=2**31 - 1, pid_starttime=1.0))
    state_store.write(RunStateRecord(id="a0", state=RunState.RUNNING,
                                pid=os.getpid(), pgid=os.getpid(),
                                pid_starttime=_read_own_starttime()))
    report = await run_mgr.recover_on_boot()
    assert report == RecoveryReport(reattached=1, interrupted=2, terminal_already=2)


@pytest.mark.asyncio
async def test_recover_emits_per_run_events(
    state_dir: Path,
) -> None:
    """Each classification should emit one structured event."""
    stream = io.StringIO()
    obs = StdlibJSONEmitter(stream=stream)
    store = RunStateStore(state_dir=state_dir)
    mgr = AsyncioRunManager(
        sim_engine=None, fuser=None, cache_codec=None, storage=None,
        obs=obs, state_store=store,
        wall_time_cap_sec=3600, particle_count_cap=500_000,
    )

    store.write(RunStateRecord(id="t", state=RunState.COMPLETED))
    store.write(RunStateRecord(id="d", state=RunState.RUNNING,
                                pid=2**31 - 1, pgid=2**31 - 1, pid_starttime=1.0))

    await mgr.recover_on_boot()

    import json
    events = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    event_names = {e["event"] for e in events}
    assert "boot.run.interrupted" in event_names
    assert "boot.recovery_complete" in event_names


@pytest.mark.asyncio
async def test_recover_handles_corrupt_record_gracefully(
    run_mgr: AsyncioRunManager, state_dir: Path, state_store: RunStateStore,
) -> None:
    """A corrupt JSON file in the state dir must not crash recovery."""
    (state_dir / "corrupt.json").write_text("{not valid json")
    # Also include one good record so we can verify recovery still proceeds.
    state_store.write(RunStateRecord(id="r", state=RunState.COMPLETED))
    report = await run_mgr.recover_on_boot()
    # Corrupt file silently skipped by RunStateStore.scan(); good record counted.
    assert report.terminal_already == 1
