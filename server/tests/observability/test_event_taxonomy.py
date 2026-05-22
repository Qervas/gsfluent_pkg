"""End-to-end event taxonomy test.

Drives a happy-path run through AsyncioRunManager with mock collaborators
and captures every emitted event into an io.StringIO via StdlibJSONEmitter.
Asserts the full lifecycle event sequence shows up and that every event in
run scope carries run_id (auto-attached via obs.child(...)).

Replaces what would otherwise be a manual `journalctl -o json | jq`
verification — CI has no journald. The manual verification step still
lives in the Definition of Done so the operator confirms the
journald-routing also works in production.
"""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    ModelRef,
    SimGpuOomError,
    SimResult,
)

# --- in-memory test doubles ---


class _MockSim:
    async def preflight(self) -> None:
        return None

    async def run(self, recipe, model, output_dir, wall_time_sec, on_event):
        on_event.emit("sim.started", phase="mpm")
        on_event.emit("sim.completed", n_frames=2)
        return SimResult(frames_dir=Path(output_dir), n_frames=2, duration_sec=0.05)


class _OomSim:
    async def preflight(self) -> None:
        return None

    async def run(self, *a, **kw):
        raise SimGpuOomError("CUDA out of memory at frame 3")


class _SlowSim:
    async def preflight(self) -> None:
        return None

    async def run(self, recipe, model, output_dir, wall_time_sec, on_event):
        await asyncio.sleep(5.0)  # long enough to cancel
        return SimResult(frames_dir=Path(output_dir), n_frames=0, duration_sec=0.0)


def _parse_events(stream: io.StringIO) -> list[dict]:
    stream.seek(0)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.fixture
def event_stream() -> io.StringIO:
    return io.StringIO()


@pytest.fixture
def obs(event_stream: io.StringIO) -> EventEmitter:
    return StdlibJSONEmitter(stream=event_stream)


@pytest.fixture
def state_store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path / "_state" / "runs")


def _make_recipe(*, run_name: str, recipe_source_name: str = "recipe.json",
                 particles: int = 100) -> dict:
    return {
        "_run_name": run_name,
        "_recipe_source_name": recipe_source_name,
        "_particles": particles,
        "particle_count": particles,
        # Stash an output_dir hint so MockSim returns a writable path
        # without sim_home being involved.
        "_output_dir": "/tmp/gsfluent-test-taxonomy",
    }


@pytest.mark.asyncio
async def test_happy_path_emits_full_lifecycle(
    obs: EventEmitter,
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    rm = AsyncioRunManager(
        sim_engine=_MockSim(),
        fuser=None,         # not used by run_to_completion (engine-driven)
        cache_codec=None,
        storage=None,
        obs=obs,
        state_store=state_store,
        wall_time_cap_sec=60,
        particle_count_cap=1_000,
    )
    rid = await rm.submit(
        _make_recipe(run_name="happy"),
        model=ModelRef(name="t", path=tmp_path),
    )
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    seen = [e["event"] for e in events]

    # Required lifecycle sequence (order-preserving subsequence check).
    required = [
        "run.queued",
        "run.preflight_ok",
        "run.started",
        "sim.started",
        "sim.completed",
        "run.simmed",
        "run.completed",
    ]
    idx = 0
    for ev in seen:
        if idx < len(required) and ev == required[idx]:
            idx += 1
    assert idx == len(required), (
        f"missing events in lifecycle. expected sub-sequence={required!r} "
        f"emitted={seen!r}"
    )


@pytest.mark.asyncio
async def test_every_event_carries_run_id_and_sequence_name(
    obs: EventEmitter,
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    rm = AsyncioRunManager(
        sim_engine=_MockSim(),
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=obs,
        state_store=state_store,
        wall_time_cap_sec=60,
        particle_count_cap=1_000,
    )
    rid = await rm.submit(
        _make_recipe(run_name="seq-x"),
        model=ModelRef(name="seq-x", path=tmp_path),
    )
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    # Every run.* / sim.* / error.* / cell.* event must carry run_id auto-
    # attached via obs.child(run_id=..., sequence_name=...).
    for e in events:
        if e["event"].startswith(("run.", "sim.", "error.", "cell.")):
            assert e.get("run_id") == rid, f"missing run_id on event {e!r}"
            assert e.get("sequence_name") == "seq-x", (
                f"missing sequence_name on event {e!r}"
            )


@pytest.mark.asyncio
async def test_sim_error_emits_error_sim_event_and_run_failed(
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    obs = StdlibJSONEmitter(stream=event_stream)
    rm = AsyncioRunManager(
        sim_engine=_OomSim(),
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=obs,
        state_store=state_store,
        wall_time_cap_sec=60,
        particle_count_cap=1_000,
    )
    rid = await rm.submit(
        _make_recipe(run_name="oom-run"),
        model=ModelRef(name="t", path=tmp_path),
    )
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    kinds = [e["event"] for e in events]
    assert "error.sim.gpu_oom" in kinds, f"emitted={kinds!r}"
    assert "run.failed" in kinds, f"emitted={kinds!r}"
    # Spec invariant: every error has exactly one structured event at its boundary.
    assert kinds.count("error.sim.gpu_oom") == 1


@pytest.mark.asyncio
async def test_cancel_emits_cancelling_and_cancelled(
    obs: EventEmitter,
    event_stream: io.StringIO,
    state_store: RunStateStore,
    tmp_path: Path,
) -> None:
    rm = AsyncioRunManager(
        sim_engine=_SlowSim(),
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=obs,
        state_store=state_store,
        wall_time_cap_sec=60,
        particle_count_cap=1_000,
    )
    rid = await rm.submit(
        _make_recipe(run_name="cancel-me"),
        model=ModelRef(name="t", path=tmp_path),
    )
    await asyncio.sleep(0.05)  # let the task actually start
    await rm.cancel(rid)
    await rm.wait_for(rid)

    events = _parse_events(event_stream)
    kinds = [e["event"] for e in events]
    assert "run.cancelling" in kinds, f"emitted={kinds!r}"
    assert "run.cancelled" in kinds, f"emitted={kinds!r}"
