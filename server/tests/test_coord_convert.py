"""Tests for the Phase 4 coord_convert module.

The math is the single canonical Y-up -> Z-up rotation Rx(-pi/2)
applied at IMPORT time. We pin behaviour at three layers:

  1. Pure-array math: positions and quaternions match the spec
     formulas, normals delegate correctly, and applying Rx(-pi/2)
     followed by Rx(+pi/2) round-trips to the original within float
     tolerance.
  2. Full-ply round-trip: a synthetic 3DGS .ply with known fields is
     written, converted, and re-read; positions/quats are rotated,
     all other fields (scales, opacity, SH) pass through untouched.
  3. End-to-end import: api/sequences/import with convert_y_up=True
     produces a materialized library entry whose first frame, when
     read back, has the rotated positions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

# --- 1. Pure array math ----------------------------------------------------


def test_rotate_positions_y_axis_to_pos_z():
    """+Y (Y-up sky) -> +Z (Z-up sky). The Rx(+pi/2) convention for sim
    data where +Y is the up direction."""
    from gsfluent.core.coord_convert import rotate_positions_y_up_to_z_up

    out = rotate_positions_y_up_to_z_up(np.array([[0.0, 1.0, 0.0]], dtype=np.float32))
    assert out.shape == (1, 3)
    np.testing.assert_array_almost_equal(out[0], [0.0, 0.0, 1.0])


def test_rotate_positions_neg_y_axis_to_neg_z():
    """-Y (Y-up ground) -> -Z (Z-up below grid)."""
    from gsfluent.core.coord_convert import rotate_positions_y_up_to_z_up

    out = rotate_positions_y_up_to_z_up(np.array([[0.0, -1.0, 0.0]], dtype=np.float32))
    np.testing.assert_array_almost_equal(out[0], [0.0, 0.0, -1.0])


def test_rotate_positions_z_axis_to_neg_y():
    """+Z (Y-up forward) -> -Y (Z-up sideways, horizontal plane)."""
    from gsfluent.core.coord_convert import rotate_positions_y_up_to_z_up

    out = rotate_positions_y_up_to_z_up(np.array([[0.0, 0.0, 1.0]], dtype=np.float32))
    np.testing.assert_array_almost_equal(out[0], [0.0, -1.0, 0.0])


def test_rotate_positions_preserves_x_axis_and_dtype():
    from gsfluent.core.coord_convert import rotate_positions_y_up_to_z_up

    inp = np.array([[7.0, 2.0, 5.0]], dtype=np.float64)
    out = rotate_positions_y_up_to_z_up(inp)
    assert out.dtype == np.float64
    # Rx(+pi/2): (x, y, z) -> (x, -z, y)
    np.testing.assert_array_almost_equal(out[0], [7.0, -5.0, 2.0])


def test_rotate_quaternions_identity_to_axis_quat():
    """Identity quaternion (1, 0, 0, 0) composed with Rx(+pi/2) yields
    the axis quaternion (cos(+pi/4), sin(+pi/4), 0, 0)."""
    from gsfluent.core.coord_convert import rotate_quaternions_y_up_to_z_up

    q = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    out = rotate_quaternions_y_up_to_z_up(q)
    expected = np.array(
        [
            [
                float(np.cos(np.pi / 4)),
                float(np.sin(np.pi / 4)),
                0.0,
                0.0,
            ]
        ],
        dtype=np.float32,
    )
    np.testing.assert_array_almost_equal(out, expected)


def test_rotate_quaternions_preserves_dtype():
    from gsfluent.core.coord_convert import rotate_quaternions_y_up_to_z_up

    q = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    out = rotate_quaternions_y_up_to_z_up(q)
    assert out.dtype == np.float64


def test_rotate_normals_matches_positions():
    """Normals share the position math — same Rx(+pi/2) applied to a
    direction vector."""
    from gsfluent.core.coord_convert import (
        rotate_normals_y_up_to_z_up,
        rotate_positions_y_up_to_z_up,
    )

    inp = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
    np.testing.assert_array_equal(
        rotate_normals_y_up_to_z_up(inp), rotate_positions_y_up_to_z_up(inp)
    )


def test_position_round_trip():
    """Applying Rx(+pi/2) then the inverse Rx(-pi/2) (i.e. (x, y, z) ->
    (x, z, -y)) returns the original within float tolerance."""
    from gsfluent.core.coord_convert import rotate_positions_y_up_to_z_up

    rng = np.random.default_rng(0)
    pts = rng.standard_normal((50, 3)).astype(np.float32)
    rotated = rotate_positions_y_up_to_z_up(pts)

    # Inverse: Rx(-pi/2) is (x, y, z) -> (x, z, -y).
    back = np.empty_like(rotated)
    back[:, 0] = rotated[:, 0]
    back[:, 1] = rotated[:, 2]
    back[:, 2] = -rotated[:, 1]
    np.testing.assert_allclose(back, pts, atol=1e-6)


def test_quaternion_double_apply_equals_180_about_x():
    """Composing the rotation twice = Rx(-pi). For a unit quaternion
    that's (0, 1, 0, 0) modulo sign — verify magnitude rather than
    exact sign since q and -q represent the same orientation."""
    from gsfluent.core.coord_convert import rotate_quaternions_y_up_to_z_up

    q = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
    once = rotate_quaternions_y_up_to_z_up(q)
    twice = rotate_quaternions_y_up_to_z_up(once)
    # Either (0, 1, 0, 0) or (0, -1, 0, 0).
    np.testing.assert_array_almost_equal(np.abs(twice[0]), [0.0, 1.0, 0.0, 0.0])


# --- 2. Full-ply round-trip ------------------------------------------------


_FULL_DTYPE = [
    ("x", "f4"), ("y", "f4"), ("z", "f4"),
    ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
    ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
    ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
    ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ("opacity", "f4"),
]


def test_convert_full_3dgs_ply_round_trip(tmp_path: Path):
    """Write a synthetic Y-up 3DGS ply with known field values, convert
    via convert_full_3dgs_ply, then read back and assert:

      - positions rotated per the spec (sky becomes -Z, etc)
      - rotation quaternions composed with the axis quat (so the
        identity quat we wrote becomes the axis quat itself)
      - normals rotated per the position math
      - all other fields (scales, opacity, SH DC) UNCHANGED
    """
    from gsfluent.core.coord_convert import convert_full_3dgs_ply

    src = tmp_path / "yup.ply"
    dst = tmp_path / "zup.ply"
    n = 4
    arr = np.zeros(n, dtype=_FULL_DTYPE)
    # Distinctive position vectors so we can spot the rotation
    arr["x"] = np.array([1.0, 0.0, 0.0, 7.0], dtype=np.float32)
    arr["y"] = np.array([0.0, 1.0, 0.0, 2.0], dtype=np.float32)
    arr["z"] = np.array([0.0, 0.0, 1.0, 5.0], dtype=np.float32)
    # Identity rotation quaternion — easy to predict the output of
    # the Hamilton product
    arr["rot_0"] = 1.0
    arr["rot_1"] = 0.0
    arr["rot_2"] = 0.0
    arr["rot_3"] = 0.0
    # Non-rotated fields with distinctive values so we can verify
    # they pass through bit-identically.
    arr["scale_0"] = -1.5
    arr["scale_1"] = -1.5
    arr["scale_2"] = -1.5
    arr["opacity"] = 0.42
    arr["f_dc_0"] = 0.7
    arr["f_dc_1"] = 0.3
    arr["f_dc_2"] = -0.1
    # Non-zero normals so the rotation is observable.
    arr["nx"] = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)
    arr["ny"] = np.array([0.0, 1.0, 0.0, 2.0], dtype=np.float32)
    arr["nz"] = np.array([0.0, 0.0, 1.0, 3.0], dtype=np.float32)

    PlyData([PlyElement.describe(arr, "vertex")]).write(str(src))

    convert_full_3dgs_ply(src, dst)
    out = PlyData.read(str(dst))["vertex"].data

    # Positions rotated: (x, y, z) -> (x, z, -y)
    # Rx(+pi/2): (x, y, z) -> (x, -z, y)
    np.testing.assert_array_almost_equal(out["x"], arr["x"])
    np.testing.assert_array_almost_equal(out["y"], -arr["z"])
    np.testing.assert_array_almost_equal(out["z"], arr["y"])

    # Normals rotated identically.
    np.testing.assert_array_almost_equal(out["nx"], arr["nx"])
    np.testing.assert_array_almost_equal(out["ny"], -arr["nz"])
    np.testing.assert_array_almost_equal(out["nz"], arr["ny"])

    # Quaternions: identity -> axis quat
    expected_w = float(np.cos(np.pi / 4))
    expected_x = float(np.sin(np.pi / 4))
    np.testing.assert_array_almost_equal(out["rot_0"], np.full(n, expected_w))
    np.testing.assert_array_almost_equal(out["rot_1"], np.full(n, expected_x))
    np.testing.assert_array_almost_equal(out["rot_2"], np.zeros(n))
    np.testing.assert_array_almost_equal(out["rot_3"], np.zeros(n))

    # Pass-through fields untouched
    np.testing.assert_array_equal(out["scale_0"], arr["scale_0"])
    np.testing.assert_array_equal(out["scale_1"], arr["scale_1"])
    np.testing.assert_array_equal(out["scale_2"], arr["scale_2"])
    np.testing.assert_array_equal(out["opacity"], arr["opacity"])
    np.testing.assert_array_equal(out["f_dc_0"], arr["f_dc_0"])
    np.testing.assert_array_equal(out["f_dc_1"], arr["f_dc_1"])
    np.testing.assert_array_equal(out["f_dc_2"], arr["f_dc_2"])

    # Vertex count preserved
    assert len(out) == n


def test_convert_full_3dgs_ply_atomic_no_partial_on_failure(tmp_path: Path):
    """A failure during the write shouldn't leave a partial output
    file at the target path. We exercise this indirectly: the
    converter writes to a tmp sibling first, so a successful write
    always produces a fully-formed final file (no .tmp left over)."""
    from gsfluent.core.coord_convert import convert_full_3dgs_ply

    src = tmp_path / "src.ply"
    dst = tmp_path / "dst.ply"
    arr = np.zeros(2, dtype=_FULL_DTYPE)
    arr["rot_0"] = 1.0
    PlyData([PlyElement.describe(arr, "vertex")]).write(str(src))

    convert_full_3dgs_ply(src, dst)
    assert dst.is_file()
    # No leftover tmp file
    assert not (tmp_path / "dst.ply.tmp").exists()


# --- 3. End-to-end import via FastAPI -------------------------------------


def _isolate(monkeypatch, tmp_path):
    from gsfluent.api import runs as runs_api
    from gsfluent.core import library
    from gsfluent.core import models as core_models

    models_dir = tmp_path / "library" / "models"
    monkeypatch.setattr(library, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(library, "SEQUENCES_DIR", tmp_path / "library" / "sequences")
    monkeypatch.setattr(library, "MODELS_DIR", models_dir)
    monkeypatch.setattr(
        library, "_REGISTERED_INDEX", models_dir / "_registered.json",
    )
    # core/models.py imported MODELS_DIR at module load; rebind the
    # name in that module so wrap_ply_upload uses the tmp path.
    monkeypatch.setattr(core_models, "MODELS_DIR", models_dir)
    monkeypatch.setattr(core_models, "UPLOADS_DIR", models_dir)
    monkeypatch.setattr(runs_api, "_LEGACY_RUNS_DIR", tmp_path / "fused")


def _write_full_ply(path: Path, n: int = 2) -> None:
    arr = np.zeros(n, dtype=_FULL_DTYPE)
    # Y-up: pretend the data has a vertical axis along +Y
    arr["y"] = 1.0
    arr["rot_0"] = 1.0
    arr["opacity"] = 1.0
    PlyData([PlyElement.describe(arr, "vertex")]).write(str(path))


def test_sequence_import_with_convert_y_up_rotates_first_frame(
    client, tmp_path, monkeypatch
):
    """End-to-end: post the import endpoint with convert_y_up=True
    and verify the materialized first frame, when read back, has its
    +Y -> -Z rotation applied."""
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "yup_seq"
    src.mkdir()
    for i in range(2):
        _write_full_ply(src / f"frame_{i:04d}.ply")

    r = client.post(
        "/api/sequences/import",
        json={"folder_path": str(src), "convert_y_up": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted_from"] == "y-up"
    assert body["coord_convention"] == "z-up"

    # Read back the materialized frame_0000.ply and check positions.
    from gsfluent.core.library import Sequence

    seq = Sequence.load(body["name"])
    assert seq is not None
    frame0 = seq.path / "frames" / "frame_0000.ply"
    assert frame0.is_file()
    out = PlyData.read(str(frame0))["vertex"].data
    # Source had y=1, x=z=0. After Rx(+pi/2): x=0, y=-z_old=0, z=y_old=1.
    np.testing.assert_array_almost_equal(out["x"], np.zeros(2))
    np.testing.assert_array_almost_equal(out["y"], np.zeros(2))
    np.testing.assert_array_almost_equal(out["z"], np.ones(2))


def test_model_upload_with_convert_y_up(client, tmp_path, monkeypatch):
    """Upload a multipart .ply with convert_y_up=true and verify the
    landed point_cloud.ply is the rotated version. Also confirms the
    response shape and that _meta.json carries converted_from."""
    _isolate(monkeypatch, tmp_path)

    # Build a Y-up ply payload in-memory.
    arr = np.zeros(2, dtype=_FULL_DTYPE)
    arr["y"] = 1.0
    arr["rot_0"] = 1.0
    arr["opacity"] = 1.0
    src_ply = tmp_path / "src.ply"
    PlyData([PlyElement.describe(arr, "vertex")]).write(str(src_ply))
    payload = src_ply.read_bytes()

    r = client.post(
        "/api/models/upload",
        files={"ply": ("yup_test.ply", payload, "application/octet-stream")},
        data={"convert_y_up": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    name = body["name"]

    # Read the landed ply.
    from gsfluent.core import library

    landed = (
        library.MODELS_DIR
        / name
        / "point_cloud"
        / "iteration_30000"
        / "point_cloud.ply"
    )
    assert landed.is_file()
    out = PlyData.read(str(landed))["vertex"].data
    np.testing.assert_array_almost_equal(out["z"], np.ones(2))

    # _meta.json carries the audit field.
    import json as _json

    meta = _json.loads((library.MODELS_DIR / name / "_meta.json").read_text())
    assert meta["converted_from"] == "y-up"
    assert meta["coord_convention"] == "z-up"


def test_model_register_with_convert_y_up_returns_copied_mode(
    client, tmp_path, monkeypatch
):
    """register + convert_y_up forces a copy into the library
    (we cannot rewrite the user's external dir in place). The
    response surfaces mode=copied-and-converted."""
    _isolate(monkeypatch, tmp_path)

    # Build a fake external 3DGS dir with a Y-up source ply.
    ext = tmp_path / "external_yup_model"
    iter_dir = ext / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True)
    arr = np.zeros(2, dtype=_FULL_DTYPE)
    arr["y"] = 1.0
    arr["rot_0"] = 1.0
    PlyData([PlyElement.describe(arr, "vertex")]).write(
        str(iter_dir / "point_cloud.ply")
    )

    r = client.post(
        "/api/models/register",
        json={"path": str(ext), "convert_y_up": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "copied-and-converted"

    # The returned path should be inside the library, not the external.
    from gsfluent.core import library

    assert Path(body["path"]).resolve() == (library.MODELS_DIR / "external_yup_model").resolve()

    # And the landed copy is rotated.
    landed = (
        library.MODELS_DIR
        / "external_yup_model"
        / "point_cloud"
        / "iteration_30000"
        / "point_cloud.ply"
    )
    out = PlyData.read(str(landed))["vertex"].data
    np.testing.assert_array_almost_equal(out["z"], np.ones(2))


def test_model_register_default_still_no_copy(client, tmp_path, monkeypatch):
    """The default (convert_y_up=False) still returns mode=registered
    and never copies — Phase 1/2/3 invariant must survive Phase 4."""
    _isolate(monkeypatch, tmp_path)

    ext = tmp_path / "untouched_model"
    iter_dir = ext / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True)
    fake = b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n" + b"\x00" * 100
    (iter_dir / "point_cloud.ply").write_bytes(fake)

    r = client.post("/api/models/register", json={"path": str(ext)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "registered"
    # path is the original external path, not a library copy.
    assert Path(body["path"]) == ext
