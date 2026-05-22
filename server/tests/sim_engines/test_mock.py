"""Tests for the MockSimulationEngine — deterministic test fixture."""
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.protocols.sim import (
    ModelRef,
    SimCrashedError,
    SimGpuOomError,
    SimResult,
    SimUnstableRecipeError,
)


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._ctx: dict = {}

    def emit(self, event: str, **context) -> None:
        merged = {**self._ctx, **context}
        self.events.append((event, merged))

    def child(self, **context) -> "_RecordingEmitter":
        new = _RecordingEmitter()
        new.events = self.events
        new._ctx = {**self._ctx, **context}
        return new


@pytest.fixture
def model(tmp_path: Path) -> ModelRef:
    md = tmp_path / "model"
    md.mkdir()
    return ModelRef(name="model", path=md)


@pytest.mark.asyncio
async def test_mock_preflight_is_a_no_op() -> None:
    eng = MockSimulationEngine()
    await eng.preflight()  # should not raise


@pytest.mark.asyncio
async def test_mock_run_writes_n_frames(tmp_path: Path, model: ModelRef) -> None:
    eng = MockSimulationEngine(n_frames=5)
    result = await eng.run(
        recipe={},
        model=model,
        output_dir=tmp_path / "out",
        wall_time_sec=60,
        on_event=_RecordingEmitter(),
    )
    assert isinstance(result, SimResult)
    assert result.n_frames == 5
    # Phase 2 mock writes sim_NNNN.ply to output_dir/sim/ — mirrors real sim output.
    files = sorted(result.frames_dir.glob("sim_*.ply"))
    assert len(files) == 5


@pytest.mark.asyncio
async def test_mock_run_emits_lifecycle_events(
    tmp_path: Path, model: ModelRef
) -> None:
    em = _RecordingEmitter()
    eng = MockSimulationEngine(n_frames=2)
    await eng.run(
        recipe={}, model=model, output_dir=tmp_path / "out",
        wall_time_sec=60, on_event=em,
    )
    event_names = [e[0] for e in em.events]
    # Phase 2 mock emits sim.started + sim.frame_written + sim.completed.
    assert "sim.started" in event_names
    assert "sim.completed" in event_names


@pytest.mark.asyncio
async def test_mock_run_raises_when_configured_to_fail_gpu_oom(
    tmp_path: Path, model: ModelRef
) -> None:
    eng = MockSimulationEngine(fail_with="sim.gpu_oom")
    with pytest.raises(SimGpuOomError):
        await eng.run(
            recipe={}, model=model, output_dir=tmp_path / "out",
            wall_time_sec=60, on_event=_RecordingEmitter(),
        )


@pytest.mark.asyncio
async def test_mock_run_raises_when_configured_to_fail_unstable(
    tmp_path: Path, model: ModelRef
) -> None:
    eng = MockSimulationEngine(fail_with="sim.unstable_recipe")
    with pytest.raises(SimUnstableRecipeError):
        await eng.run(
            recipe={}, model=model, output_dir=tmp_path / "out",
            wall_time_sec=60, on_event=_RecordingEmitter(),
        )


@pytest.mark.asyncio
async def test_mock_run_raises_crashed_when_configured(
    tmp_path: Path, model: ModelRef
) -> None:
    eng = MockSimulationEngine(fail_with="sim.crashed")
    with pytest.raises(SimCrashedError):
        await eng.run(
            recipe={}, model=model, output_dir=tmp_path / "out",
            wall_time_sec=60, on_event=_RecordingEmitter(),
        )


@pytest.mark.asyncio
async def test_mock_respects_delay_sec(
    tmp_path: Path, model: ModelRef
) -> None:
    import time
    eng = MockSimulationEngine(n_frames=3, delay_sec=0.05)
    t0 = time.monotonic()
    await eng.run(
        recipe={}, model=model, output_dir=tmp_path / "out",
        wall_time_sec=60, on_event=_RecordingEmitter(),
    )
    elapsed = time.monotonic() - t0
    # 3 frames * 0.05s = 0.15s; allow generous slack for CI.
    assert elapsed >= 0.10
