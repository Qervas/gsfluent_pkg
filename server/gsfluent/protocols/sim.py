"""SimulationEngine Protocol — layer 2.

Runs the MPM (or other physics) sim to produce per-frame particle state.
Concrete: MPMSimulationEngine (Phase 3, absorbs run_sim.sh logic) and
MockSimulationEngine (test fixture). Cancellable via SIGTERM to PG.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from gsfluent.protocols.observability import EventEmitter


class SimError(Exception):
    """Base for simulation-layer errors."""


class SimEnvMissingError(SimError):
    """$GSFLUENT_SIM_HOME unset or directory missing."""


class SimInterpreterMissingError(SimError):
    """$GSFLUENT_SIM_PYTHON unset or not on PATH."""


class GPUUnavailableError(SimError):
    """nvidia-smi reports no CUDA-capable device, or GPU is otherwise unreachable."""


class SimWallTimeExceededError(SimError):
    """Sim ran past wall_time_sec; killed by orchestrator timeout."""


class SimGpuOomError(SimError):
    """Sim allocated more GPU memory than available."""


class SimUnstableRecipeError(SimError):
    """Numerical instability detected via stderr classifier."""


class SimCrashedError(SimError):
    """Non-zero exit, classifier did not match a known pattern."""


# ValidatedRecipe: a recipe dict that has already been Pydantic-validated
# and cap-checked at the API boundary. Concrete impls treat it as
# trusted-shape; runtime values still need defensive handling.
ValidatedRecipe = dict[str, Any]


@dataclass(frozen=True)
class ModelRef:
    """Identifier + filesystem location of a 3DGS model."""
    name: str
    path: Path


@dataclass(frozen=True)
class SimResult:
    """Returned by SimulationEngine.run() on success."""
    frames_dir: Path        # directory containing sim_*.ply files
    n_frames: int
    duration_sec: float


@runtime_checkable
class SimulationEngine(Protocol):
    """Run a physics sim from a validated recipe to per-frame particle state."""

    async def preflight(self) -> None:
        """Raise typed error if environment cannot run a sim.
        SimEnvMissingError / SimInterpreterMissingError / GPUUnavailableError."""
        ...

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        """Run sim to completion or raise typed SimError.

        Must be cancellable via cooperative cancellation (asyncio.CancelledError
        on outer task) OR external SIGTERM to the process group.

        Emits events through on_event at sim lifecycle transitions
        (sim.started, sim.completed). Caller (RunManager) translates
        these to run.* events with run_id attached via .child().
        """
        ...
