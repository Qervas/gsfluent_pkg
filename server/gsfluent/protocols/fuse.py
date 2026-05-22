"""Fuser Protocol — layer 3.

Combines a reference 3DGS scene with per-frame sim particle positions
to produce per-frame fully-attributed splat frames. Concrete: KNNKabschFuser
(Phase 2; moved from server/tools/fuse_to_full_ply.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable


class FuseError(Exception):
    """Base for fuser errors."""


class FuseDegenerateClusterError(FuseError):
    """K-NN cluster degenerate; Kabsch cannot solve."""


class FuseNonFiniteInputError(FuseError):
    """Particle frame contains NaN/Inf positions."""


# ParticleFrame: a sequence of (x, y, z) tuples or an (N, 3) ndarray.
# Kept loose; concrete impls type-narrow as needed.
ParticleFrame = Any
SplatFrame = dict[str, Any]  # same shape as protocols.cache.SplatFrame


@dataclass(frozen=True)
class Correspondence:
    """Reference-to-particle mapping built once per sequence.

    Reused for every subsequent frame's fuse_frame() call.
    """
    reference_ply_path: Path
    indices: tuple[int, ...]
    extent: float


@runtime_checkable
class Fuser(Protocol):
    """Build per-frame splat frames from sim particle positions."""

    def build_correspondence(
        self,
        reference_ply_path: Path,
        first_frame_particles: ParticleFrame,
    ) -> Correspondence:
        """Compute reference→particle mapping. One-shot per sequence.
        Raises FuseError on degenerate input."""
        ...

    def fuse_frame(
        self,
        correspondence: Correspondence,
        particle_frame: ParticleFrame,
    ) -> SplatFrame:
        """Apply correspondence + per-frame rotation.
        Raises FuseError on non-finite input or degenerate K-NN cluster."""
        ...
