"""Conformance tests for the Fuser Protocol."""
from pathlib import Path

import pytest

from gsfluent.protocols.fuse import (
    Correspondence,
    FuseError,
    Fuser,
    ParticleFrame,
)
from gsfluent.protocols.fuse import (
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


# --- Conformance over real KNNKabschFuser -----------------------------------

import numpy as np
from plyfile import PlyData, PlyElement


def _write_full_3dgs_ply_for_protocol(path, n: int = 10, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_real_fuser_satisfies_protocol() -> None:
    from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
    f: Fuser = KNNKabschFuser(k=4)
    assert isinstance(f, Fuser)


def test_real_fuser_correspondence_then_frame(tmp_path) -> None:
    from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser

    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply_for_protocol(ref_path, n=10, seed=0)
    rng = np.random.default_rng(0)
    p0 = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)
    p1 = p0 + rng.normal(scale=0.05, size=p0.shape).astype(np.float32)

    f = KNNKabschFuser(k=4)
    corr = f.build_correspondence(ref_path, p0)
    out = f.fuse_frame(corr, p1)
    assert "xyz" in out
