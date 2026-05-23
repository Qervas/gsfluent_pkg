"""Conformance tests for the RunManager Protocol."""
from collections.abc import AsyncIterator

import pytest

from gsfluent.protocols.runs import (
    CapExceededError,
    RecoveryReport,
    RunEvent,
    RunId,
    RunManager,
    RunState,
    RunStatus,
    ValidationError,
)
from gsfluent.protocols.sim import ModelRef, ValidatedRecipe


class _StubRunManager:
    def __init__(self) -> None:
        self._runs: dict[RunId, RunStatus] = {}

    async def submit(self, recipe: ValidatedRecipe, *, model: ModelRef) -> RunId:
        rid = RunId(f"run-{len(self._runs)}")
        self._runs[rid] = RunStatus(id=rid, state=RunState.QUEUED)
        return rid

    async def cancel(self, run_id: RunId) -> None:
        if run_id in self._runs:
            self._runs[run_id] = RunStatus(id=run_id, state=RunState.CANCELLED)

    async def status(self, run_id: RunId) -> RunStatus:
        return self._runs[run_id]

    async def stream_events(self, run_id: RunId) -> AsyncIterator[RunEvent]:
        async def _gen():
            yield RunEvent(event="run.queued", context={"run_id": run_id})
        return _gen()

    async def recover_on_boot(self) -> RecoveryReport:
        return RecoveryReport(reattached=0, interrupted=0, terminal_already=0)


def test_stub_satisfies_run_manager_protocol() -> None:
    rm: RunManager = _StubRunManager()
    assert isinstance(rm, RunManager)


@pytest.mark.asyncio
async def test_submit_returns_run_id() -> None:
    rm = _StubRunManager()
    rid = await rm.submit({}, model=ModelRef(name="t", path=__import__("pathlib").Path("/")))
    # RunId is a NewType wrapping str; verify the runtime supertype.
    assert isinstance(rid, str)


@pytest.mark.asyncio
async def test_cancel_transitions_state() -> None:
    from pathlib import Path
    rm = _StubRunManager()
    rid = await rm.submit({}, model=ModelRef(name="t", path=Path("/")))
    await rm.cancel(rid)
    status = await rm.status(rid)
    assert status.state == RunState.CANCELLED


def test_state_enum_has_required_members() -> None:
    expected = {"QUEUED", "STARTED", "RUNNING", "COMPLETED", "FAILED",
                "CANCELLING", "CANCELLED", "INTERRUPTED"}
    actual = {m.name for m in RunState}
    assert expected <= actual


def test_validation_and_cap_errors() -> None:
    with pytest.raises(ValidationError):
        raise ValidationError("bad recipe")
    with pytest.raises(CapExceededError):
        raise CapExceededError("too many particles")


# --- Conformance over real AsyncioRunManager --------------------------------


@pytest.fixture
def real_run_mgr(tmp_path):
    from gsfluent.core.run_manager import AsyncioRunManager
    from gsfluent.core.sim_engines.mock import MockSimulationEngine
    from gsfluent.core.state import RunStateStore

    # Self-contained stubs — the Protocol conformance check exercises the
    # Protocol surface; it does not need a real Fuser/Codec/Storage.
    class _NullEmitter:
        def emit(self, event: str, **context) -> None: pass
        def child(self, **context): return self
    class _Stub:
        def __getattr__(self, name):
            async def _aio(*a, **kw): raise NotImplementedError
            return _aio

    store = RunStateStore(state_dir=tmp_path / "state" / "runs")
    return AsyncioRunManager(
        sim_engine=MockSimulationEngine(n_frames=1, n_particles=2),
        fuser=_Stub(),
        cache_codec=_Stub(),
        storage=_Stub(),
        obs=_NullEmitter(),
        state_store=store,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


def test_real_run_mgr_satisfies_protocol(real_run_mgr) -> None:
    rm: RunManager = real_run_mgr
    assert isinstance(rm, RunManager)


@pytest.mark.asyncio
async def test_real_run_mgr_recover_on_boot_empty(real_run_mgr) -> None:
    report = await real_run_mgr.recover_on_boot()
    assert report.reattached == 0
    assert report.interrupted == 0
    assert report.terminal_already == 0
