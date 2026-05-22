"""Conformance tests for the Fuser Protocol."""
from pathlib import Path

import pytest

from gsfluent.protocols.fuse import (
    Correspondence,
    FuseError,
    Fuser,
    ParticleFrame,
    SplatFrame as FusedSplatFrame,
)


class _StubFuser:
    """Identity fuser: passes particles through as splats."""

    def build_correspondence(
        self, reference_ply_path: Path, first_frame_particles: ParticleFrame
    ) -> Correspondence:
        return Correspondence(
            reference_ply_path=reference_ply_path,
            indices=tuple(range(len(first_frame_particles))),
            extent=1.0,
        )

    def fuse_frame(
        self, correspondence: Correspondence, particle_frame: ParticleFrame
    ) -> FusedSplatFrame:
        return {"xyz": list(particle_frame)}


def test_stub_satisfies_fuser_protocol() -> None:
    fuser: Fuser = _StubFuser()
    assert isinstance(fuser, Fuser)


def test_build_correspondence_returns_correspondence() -> None:
    fuser = _StubFuser()
    corr = fuser.build_correspondence(Path("/tmp/ref.ply"), [(0.0, 0.0, 0.0)])
    assert corr.reference_ply_path == Path("/tmp/ref.ply")
    assert corr.indices == (0,)


def test_fuse_frame_returns_splat_dict() -> None:
    fuser = _StubFuser()
    corr = fuser.build_correspondence(Path("/tmp/ref.ply"), [(0.0, 0.0, 0.0)])
    result = fuser.fuse_frame(corr, [(1.0, 2.0, 3.0)])
    assert result == {"xyz": [(1.0, 2.0, 3.0)]}


def test_fuse_error_is_an_exception() -> None:
    with pytest.raises(FuseError):
        raise FuseError("synthetic")
