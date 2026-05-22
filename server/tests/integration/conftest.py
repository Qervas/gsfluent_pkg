"""Shared integration-test fixtures.

Each test wires an AsyncioRunManager around the mock_sim.sh fixture so
the actual subprocess lifecycle (PG creation, signal delivery,
escalation, wait_for timeout) is exercised end-to-end without a GPU.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.core.state import RunStateStore
from gsfluent.core.limits import CapConfig
from gsfluent.observability.jsonlog import StdlibJSONEmitter


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
MOCK_SIM_SH = FIXTURE_DIR / "mock_sim.sh"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_state" / "runs"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """A bare model dir - mock sim does not actually load it."""
    d = tmp_path / "model"
    d.mkdir()
    return d


@pytest.fixture
def event_sink() -> list:
    """Mutable list the StdlibJSONEmitter writes into; tests assert on it."""
    return []


@pytest.fixture
def emitter(event_sink: list) -> StdlibJSONEmitter:
    import io
    # Use a StringIO so the emitter writes structured events we can read back.
    stream = io.StringIO()
    em = StdlibJSONEmitter(stream=stream)
    em._test_stream = stream  # tests reach in for debugging
    return em


class SubprocessMockSimulationEngine:
    """A SimulationEngine that shells out to the mock_sim.sh fixture.

    Use this (instead of MockSimulationEngine) when the test needs to
    verify real subprocess PG signal delivery / escalation. The mock_sim.sh
    fixture is configurable via MOCK_SIM_* env vars.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = env or {}

    async def preflight(self) -> None:
        return None

    async def run(self, recipe, model, output_dir, wall_time_sec, on_event):
        import asyncio
        import os
        import time
        from gsfluent.core.run_manager import spawn_in_new_pg
        from gsfluent.protocols.sim import SimCrashedError, SimResult

        argv = [
            "bash", str(MOCK_SIM_SH),
            str(model.path),
            "--config", "/dev/null",
            "--particles", "100",
            "--output", output_dir.name,
        ]
        env = {**os.environ, **self._env}
        # Spawn in a brand-new PG just like MPMSimulationEngine does.
        proc = await spawn_in_new_pg(
            argv=argv,
            cwd="/tmp",
        )
        # Re-spawn under the custom env if needed by patching env directly;
        # spawn_in_new_pg doesn't accept env, so we do this the long way
        # when MOCK_SIM_* knobs need to be set.
        if self._env:
            # Kill the no-env proc we just made and re-create with env.
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            from asyncio.subprocess import create_subprocess_exec as _spawn
            proc = await _spawn(
                *argv,
                cwd="/tmp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=env,
            )
        pgid = os.getpgid(proc.pid)
        on_event.emit("sim.spawned", pid=proc.pid, pgid=pgid)
        rc = await proc.wait()
        on_event.emit("sim.completed", returncode=rc)
        if rc != 0:
            raise SimCrashedError(f"mock_sim.sh rc={rc}")
        frames_dir = output_dir
        return SimResult(frames_dir=frames_dir, n_frames=0, duration_sec=0.0)
