"""KNNKabschFuser unit tests. Protocol conformance lives in
tests/protocols/test_fuse_protocol.py (parametrized over impls).
"""
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.fusers.knn_kabsch import (
    KNNKabschFuser,
    _norm_xyz_to_origin_cube,
)
from gsfluent.protocols.fuse import (
    Correspondence,
    FuseError,
    FuseNonFiniteInputError,
    Fuser,
)


def _write_full_3dgs_ply(path: Path, n: int = 10, seed: int = 0) -> None:
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
    verts["opacity"] = 0.5
    verts["scale_0"] = -1.0
    verts["scale_1"] = -1.0
    verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def _write_sim_xyz_ply(path: Path, n: int = 5, seed: int = 0) -> None:
    """A 'sim_*.ply' style file — xyz only, no scales/SH/etc."""
    rng = np.random.default_rng(seed)
    verts = np.zeros(n, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    verts["x"] = rng.uniform(0, 2, n).astype(np.float32)
    verts["y"] = rng.uniform(0, 2, n).astype(np.float32)
    verts["z"] = rng.uniform(0, 2, n).astype(np.float32)
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_fuser_satisfies_protocol() -> None:
    f: Fuser = KNNKabschFuser(k=4)
    assert isinstance(f, Fuser)


def test_norm_xyz_to_origin_cube_centers_data() -> None:
    """Sanity: normalization maps the input bbox center to (1,1,1) and scales
    longest axis to 1.0."""
    xyz = np.array([[0, 0, 0], [10, 5, 2]], dtype=np.float32)
    out, center, extent = _norm_xyz_to_origin_cube(xyz)
    # After normalization, bbox center should be at (1, 1, 1).
    out_min = out.min(axis=0)
    out_max = out.max(axis=0)
    np.testing.assert_allclose((out_min + out_max) / 2, [1.0, 1.0, 1.0], atol=1e-5)
    assert extent == 10.0  # longest axis was x


def test_build_correspondence_returns_correspondence(tmp_path: Path) -> None:
    """Real fuser: build_correspondence on small synthetic ply + sim frame."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    rng = np.random.default_rng(0)
    first_frame_particles = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)

    fuser = KNNKabschFuser(k=4)
    corr = fuser.build_correspondence(ref_path, first_frame_particles)
    assert isinstance(corr, Correspondence)
    assert corr.reference_ply_path == ref_path
    assert corr.extent > 0


def test_fuse_frame_returns_dict_with_xyz(tmp_path: Path) -> None:
    """fuse_frame yields a SplatFrame dict carrying at least 'xyz'."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    rng = np.random.default_rng(0)
    p0 = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)
    p1 = p0 + rng.normal(scale=0.05, size=p0.shape).astype(np.float32)

    fuser = KNNKabschFuser(k=4)
    corr = fuser.build_correspondence(ref_path, p0)
    out = fuser.fuse_frame(corr, p1)
    assert "xyz" in out
    assert out["xyz"].shape[1] == 3


def test_fuse_frame_non_finite_input_raises(tmp_path: Path) -> None:
    """NaN positions in the particle frame raise FuseNonFiniteInputError."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    rng = np.random.default_rng(0)
    p0 = rng.uniform(0, 2, size=(5, 3)).astype(np.float32)
    bad = p0.copy()
    bad[0, 0] = np.nan

    fuser = KNNKabschFuser(k=4)
    corr = fuser.build_correspondence(ref_path, p0)
    with pytest.raises(FuseNonFiniteInputError):
        fuser.fuse_frame(corr, bad)


def test_fuser_default_k_is_8() -> None:
    """Default K matches the production-recommended value from the spec."""
    f = KNNKabschFuser()
    assert f.k == 8


def test_fuse_sequence_dir_writes_per_frame_plys(tmp_path: Path) -> None:
    """fuse_sequence_dir: drives the per-frame loop; sanity-checks output count."""
    ref_path = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref_path, n=10, seed=0)

    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    for i in range(3):
        _write_sim_xyz_ply(sim_dir / f"sim_{i:04d}.ply", n=5, seed=i)

    out_dir = tmp_path / "out"
    fuser = KNNKabschFuser(k=4)
    n_written = fuser.fuse_sequence_dir(
        reference_ply_path=ref_path,
        sim_dir=sim_dir,
        out_dir=out_dir,
    )
    assert n_written == 3
    assert (out_dir / "frame_0000.ply").is_file()
    assert (out_dir / "frame_0002.ply").is_file()
