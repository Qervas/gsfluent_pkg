"""Pin the workbench's "all stored data is Z-up" invariant in tests.

Phase guarantee: after the fuse fix + migration, every frame_*.ply
on disk is Z-up at rest. The display pipeline (frame_stream.py) is
therefore expected to NOT apply any extra rotation — a request that
came directly out of the disagreement between splat-mode (raw .ply
over HTTP) and points-mode (parse_frame_xyz).

Coverage:
  1. A synthetic Y-up full 3DGS .ply -> convert_full_3dgs_ply produces
     a file whose tall axis is Z (was Y in the input).
  2. parse_frame_xyz on the Z-up file returns positions byte-equal to
     what's on disk — no display-time rotation.
  3. parse_static_attrs on the Z-up file returns rotation matrices
     equal to the raw quaternion-derived rotation, with no extra
     basis composition.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.coord_convert import convert_full_3dgs_ply
from gsfluent.core.frame_stream import parse_frame_xyz, parse_static_attrs

_FULL_DTYPE = [
    ("x", "f4"), ("y", "f4"), ("z", "f4"),
    ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
    ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
    ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
    ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ("opacity", "f4"),
]


def _write_yup_building_ply(path: Path) -> None:
    """Synthesize a Y-up "building": positions span [-0.3, 0.3] in X,
    [0, 1] in Y (the tall axis, simulating sim's vertical), and
    [-0.2, 0.2] in Z. Add the minimum 3DGS attribute set so the file
    passes parse_static_attrs validation."""
    n = 8
    arr = np.zeros(n, dtype=_FULL_DTYPE)
    # Distinctive corner coords; Y is the tallest extent.
    arr["x"] = np.array([-0.3, 0.3, -0.3, 0.3, -0.3, 0.3, -0.3, 0.3], dtype=np.float32)
    arr["y"] = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
    arr["z"] = np.array([-0.2, -0.2, 0.2, 0.2, -0.2, -0.2, 0.2, 0.2], dtype=np.float32)
    arr["rot_0"] = 1.0      # identity quaternion
    arr["scale_0"] = -2.0
    arr["scale_1"] = -2.0
    arr["scale_2"] = -2.0
    arr["opacity"] = 1.0
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(str(path))


def test_convert_full_3dgs_ply_makes_z_the_tall_axis(tmp_path: Path):
    """Y-tallest input -> after convert_full_3dgs_ply, Z must be tallest."""
    src = tmp_path / "yup.ply"
    dst = tmp_path / "zup.ply"
    _write_yup_building_ply(src)

    # Sanity: the input really is Y-tallest.
    pre = PlyData.read(str(src))["vertex"].data
    pre_extent = np.array([
        pre["x"].max() - pre["x"].min(),
        pre["y"].max() - pre["y"].min(),
        pre["z"].max() - pre["z"].min(),
    ])
    assert pre_extent.argmax() == 1, f"fixture should be Y-tallest, got {pre_extent}"

    convert_full_3dgs_ply(src, dst)

    post = PlyData.read(str(dst))["vertex"].data
    post_extent = np.array([
        post["x"].max() - post["x"].min(),
        post["y"].max() - post["y"].min(),
        post["z"].max() - post["z"].min(),
    ])
    assert post_extent.argmax() == 2, (
        f"after conversion the tall axis should be Z, got {post_extent}"
    )
    # The tall extent magnitude should match the original Y extent.
    np.testing.assert_allclose(post_extent[2], pre_extent[1], atol=1e-6)


def test_parse_frame_xyz_no_extra_rotation_on_zup_file(tmp_path: Path):
    """Once stored data is Z-up, parse_frame_xyz must return positions
    byte-equal to what's on disk — no display-time rotation."""
    src = tmp_path / "yup.ply"
    dst = tmp_path / "zup.ply"
    _write_yup_building_ply(src)
    convert_full_3dgs_ply(src, dst)

    # Read positions raw from the Z-up file.
    raw = PlyData.read(str(dst))["vertex"].data
    raw_xyz = np.stack([raw["x"], raw["y"], raw["z"]], axis=1).astype(np.float32)

    parsed = parse_frame_xyz(dst)
    np.testing.assert_array_equal(parsed, raw_xyz)


def test_parse_static_attrs_no_basis_composition_on_zup_file(tmp_path: Path):
    """parse_static_attrs used to compose M_YUP_TO_ZUP with the
    quaternion-derived rotation. With Z-up storage that's gone — R
    must equal the raw quaternion-to-matrix conversion. Identity
    quaternion -> identity R."""
    src = tmp_path / "yup.ply"
    dst = tmp_path / "zup.ply"
    _write_yup_building_ply(src)
    convert_full_3dgs_ply(src, dst)

    attrs = parse_static_attrs(dst)
    assert attrs is not None

    # The convert step composed the identity quat with the axis quat
    # (cos(+pi/4), sin(+pi/4), 0, 0). The corresponding rotation
    # matrix is Rx(+pi/2):
    #   [1,  0,  0]
    #   [0,  0, -1]
    #   [0,  1,  0]
    expected_R = np.array(
        [[1.0, 0.0, 0.0],
         [0.0, 0.0, -1.0],
         [0.0, 1.0, 0.0]],
        dtype=np.float32,
    )
    np.testing.assert_allclose(attrs["R"][0], expected_R, atol=1e-6)

    # Sanity: the OLD parse_static_attrs would have left-multiplied this
    # by M_YUP_TO_ZUP. With that composition the rotation would land at
    # something different from the matrix we just asserted; this guards
    # against re-introducing the display-time composition.
    old_legacy_M = np.array(
        [[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float32
    )
    legacy_R = old_legacy_M @ expected_R
    assert not np.allclose(attrs["R"][0], legacy_R)
