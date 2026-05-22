"""Tests for the ply parser. WebSocket streaming itself is not unit-tested
at this layer; the Phase 6 Playwright E2E test exercises the full path."""
import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.frame_stream import parse_frame_xyz, parse_static_attrs


def _write_minimal_ply(path, n=10):
    """Write a ply with only x/y/z. parse_static_attrs should return None
    for these (no SH/scale/rot)."""
    vertex = np.zeros(n, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    vertex["x"] = np.arange(n, dtype=np.float32)
    vertex["y"] = np.arange(n, dtype=np.float32) * 2
    vertex["z"] = np.arange(n, dtype=np.float32) * 3
    el = PlyElement.describe(vertex, "vertex")
    PlyData([el], text=False).write(str(path))


def _write_full_3dgs_ply(path, n=10):
    """Write a ply with the full 3DGS attribute set."""
    dtype = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
    ]
    vertex = np.zeros(n, dtype=dtype)
    vertex["x"] = np.arange(n, dtype=np.float32)
    vertex["y"] = np.arange(n, dtype=np.float32)
    vertex["z"] = np.arange(n, dtype=np.float32)
    vertex["scale_0"] = -2.0   # log scale, exp(-2) ≈ 0.135
    vertex["scale_1"] = -2.0
    vertex["scale_2"] = -2.0
    vertex["rot_0"] = 1.0      # identity quat (w=1, xyz=0)
    vertex["f_dc_0"] = 0.5     # arbitrary; clip([0.5*0.282 + 0.5], 0, 1) = ~0.641
    vertex["f_dc_1"] = 0.0
    vertex["f_dc_2"] = -0.5
    vertex["opacity"] = 1.0    # sigmoid(1.0) ≈ 0.731
    el = PlyElement.describe(vertex, "vertex")
    PlyData([el], text=False).write(str(path))


def test_parse_frame_xyz_returns_positions_unchanged(tmp_path):
    """Stored data is Z-up at rest (workbench invariant); the parser
    must return positions exactly as they sit on disk, no display-time
    rotation."""
    p = tmp_path / "frame_0000.ply"
    _write_minimal_ply(p, n=3)
    xyz = parse_frame_xyz(p)
    assert xyz.shape == (3, 3)
    assert xyz.dtype == np.float32
    # original (x, y, z) = (i, 2i, 3i) — no rotation applied.
    np.testing.assert_allclose(xyz[1], [1.0, 2.0, 3.0], atol=1e-6)


def test_parse_static_attrs_returns_none_for_xyz_only_ply(tmp_path):
    p = tmp_path / "minimal.ply"
    _write_minimal_ply(p)
    assert parse_static_attrs(p) is None


def test_parse_static_attrs_extracts_full_3dgs(tmp_path):
    p = tmp_path / "full.ply"
    _write_full_3dgs_ply(p, n=5)
    attrs = parse_static_attrs(p)
    assert attrs is not None
    assert attrs["n"] == 5
    # scales: exp(-2) ≈ 0.135
    np.testing.assert_allclose(attrs["scales"], np.exp(-2.0), atol=1e-5)
    # R matrix: identity quaternion -> identity rotation. No display-time
    # basis rotation is composed in (data is Z-up at rest), so R[0] is I.
    np.testing.assert_allclose(attrs["R"][0], np.eye(3, dtype=np.float32), atol=1e-6)
    # rgb: clip(0.5 * 0.282... + 0.5, 0, 1) for f_dc_0 channel
    expected_r = 0.5 * 0.28209479177387814 + 0.5
    np.testing.assert_allclose(attrs["rgb"][0, 0], expected_r, atol=1e-5)
    # opacity: sigmoid(1.0) = 1/(1+exp(-1)) ≈ 0.7311
    np.testing.assert_allclose(attrs["opacity"][0], 1.0 / (1.0 + np.exp(-1.0)), atol=1e-5)


def test_parse_static_attrs_handles_nan_quaternion(tmp_path):
    """A NaN in the quaternion should not propagate to the rotation matrix."""
    p = tmp_path / "nan_quat.ply"
    _write_full_3dgs_ply(p, n=2)
    # Inject NaN into one quaternion
    data = PlyData.read(str(p))
    arr = np.array(data["vertex"].data, copy=True)
    arr["rot_0"][0] = np.nan
    out = tmp_path / "nan_quat_2.ply"
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(str(out))
    attrs = parse_static_attrs(out)
    assert attrs is not None
    # The R matrix for the NaN row should be all-finite (identity-like, not NaN)
    assert np.all(np.isfinite(attrs["R"][0]))


def test_parse_static_attrs_normalizes_quaternions(tmp_path):
    """Even if the ply stores non-unit quaternions, the result rotation
    matrix should still be a proper rotation."""
    src = tmp_path / "seed.ply"
    _write_full_3dgs_ply(src, n=2)
    # Read into a fresh numpy copy (plyfile mmap's the source; we must not
    # rewrite the file while a buffer is still pointing into its mapping).
    v = np.array(PlyData.read(str(src))["vertex"].data)
    v["rot_0"] = 2.0   # norm becomes 2
    p = tmp_path / "scaled_quat.ply"
    PlyData([PlyElement.describe(v, "vertex")], text=False).write(str(p))
    attrs = parse_static_attrs(p)
    # R should still satisfy R @ R.T ≈ I — the quat is normalized first.
    R = attrs["R"][0]
    should_be_I = R @ R.T
    np.testing.assert_allclose(should_be_I, np.eye(3), atol=1e-5)
