"""MockSimulationEngine - test fixture conforming to the SimulationEngine Protocol.

Writes synthetic sim_*.ply frames (xyz only, matching the real sim's output
shape) to the requested output directory and returns a SimResult. No real
GPU, no shell. Used by Phase 2's end-to-end smoke test and any future
integration test that needs a deterministic sim stand-in.

Configurable via constructor args:
    n_frames: int               how many sim_*.ply files to emit (default 5)
    n_particles: int            particles per frame (default 100)
    seed: int                   RNG seed (default 0) for reproducible particle positions
    delay_sec: float            per-frame sleep — useful for cancel/timeout tests
                                 (Phase 3 uses this; Phase 2's smoke test leaves it 0)
    fail_with: str | None       one of "sim.gpu_oom" / "sim.unstable_recipe" /
                                "sim.crashed", or None for success (default None)
    fail_after_frame: int       emit this many frames, then raise (default 0)
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement

from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    ModelRef,
    SimCrashedError,
    SimGpuOomError,
    SimResult,
    SimUnstableRecipeError,
    ValidatedRecipe,
)


class MockSimulationEngine:
    """Deterministic SimulationEngine impl for tests. No GPU required."""

    def __init__(
        self,
        *,
        n_frames: int = 5,
        n_particles: int = 100,
        seed: int = 0,
        delay_sec: float = 0.0,
        fail_with: str | None = None,
        fail_after_frame: int = 0,
    ) -> None:
        self.n_frames = n_frames
        self.n_particles = n_particles
        self.seed = seed
        self.delay_sec = delay_sec
        self._fail_with = fail_with
        self._fail_after_frame = fail_after_frame

    async def preflight(self) -> None:
        """Mock preflight is a no-op — environment is always considered ready."""
        return None

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        """Generate synthetic per-frame sim_*.ply files.

        If fail_with is set, raise the matching typed SimError instead of
        completing — after emitting fail_after_frame frames first.
        """
        frames_dir = output_dir / "sim"
        frames_dir.mkdir(parents=True, exist_ok=True)
        on_event.emit("sim.started", n_frames=self.n_frames)

        rng = np.random.default_rng(self.seed)
        # Frame 0: random particles uniformly in [0, 2]^3 (normalized sim cube).
        base = rng.uniform(0.0, 2.0, size=(self.n_particles, 3)).astype(np.float32)

        for t in range(self.n_frames):
            if self._fail_with and t == self._fail_after_frame:
                self._raise_classified()
            # Tiny per-frame jitter so consecutive frames differ.
            jitter = rng.normal(scale=0.01, size=base.shape).astype(np.float32)
            xyz = base + jitter * t
            verts = np.zeros(
                self.n_particles,
                dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")],
            )
            verts["x"] = xyz[:, 0]
            verts["y"] = xyz[:, 1]
            verts["z"] = xyz[:, 2]
            out_path = frames_dir / f"sim_{t:04d}.ply"
            PlyData([PlyElement.describe(verts, "vertex")], text=False).write(out_path)
            on_event.emit("sim.frame_written", frame_index=t)
            if self.delay_sec:
                await asyncio.sleep(self.delay_sec)

        if self._fail_with and self._fail_after_frame >= self.n_frames:
            self._raise_classified()

        on_event.emit("sim.completed", n_frames=self.n_frames)
        return SimResult(
            frames_dir=frames_dir,
            n_frames=self.n_frames,
            duration_sec=0.0,
        )

    def _raise_classified(self) -> None:
        msg = f"MockSimulationEngine configured to fail with {self._fail_with}"
        if self._fail_with == "sim.gpu_oom":
            raise SimGpuOomError(msg)
        if self._fail_with == "sim.unstable_recipe":
            raise SimUnstableRecipeError(msg)
        # default: generic crash
        raise SimCrashedError(msg)
