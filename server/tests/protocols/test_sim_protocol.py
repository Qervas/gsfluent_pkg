"""Conformance tests for the SimulationEngine Protocol."""
from pathlib import Path

import pytest

from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    ModelRef,
    SimError,
    SimEnvMissingError,
    SimInterpreterMissingError,
    SimResult,
    SimulationEngine,
    SimWallTimeExceededError,
    ValidatedRecipe,
)


class _StubEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context) -> "_StubEmitter": return self


class _StubSimEngine:
    """Stub SimEngine that does nothing real."""

    async def preflight(self) -> None:
        return None

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        return SimResult(frames_dir=output_dir / "frames", n_frames=0, duration_sec=0.0)


def test_stub_satisfies_sim_protocol() -> None:
    eng: SimulationEngine = _StubSimEngine()
    assert isinstance(eng, SimulationEngine)


@pytest.mark.asyncio
async def test_stub_preflight_returns_none() -> None:
    eng = _StubSimEngine()
    assert (await eng.preflight()) is None


@pytest.mark.asyncio
async def test_stub_run_returns_sim_result() -> None:
    eng = _StubSimEngine()
    result = await eng.run(
        recipe={"any": "shape"},
        model=ModelRef(name="test", path=Path("/tmp/model")),
        output_dir=Path("/tmp/out"),
        wall_time_sec=60,
        on_event=_StubEmitter(),
    )
    assert isinstance(result, SimResult)
    assert result.n_frames == 0


def test_sim_error_hierarchy() -> None:
    assert issubclass(SimEnvMissingError, SimError)
    assert issubclass(SimInterpreterMissingError, SimError)
    assert issubclass(GPUUnavailableError, SimError)
    assert issubclass(SimWallTimeExceededError, SimError)
