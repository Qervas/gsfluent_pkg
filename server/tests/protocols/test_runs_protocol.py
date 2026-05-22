"""Conformance tests for the RunManager Protocol."""
from typing import AsyncIterator

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
