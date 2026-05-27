"""Kabsch per-splat rotation in KNNKabschFuser.

The production fuser used to be translation-only: every reference splat kept
its rest quaternion forever, so deforming surfaces shredded (the anisotropic
lobe stopped hugging the surface). These tests pin the restored behaviour:
each splat now picks up the local rigid rotation of its K nearest sim
particles (rest -> current) via weighted Kabsch / orthogonal Procrustes, and
that rotation is composed onto the rest quaternion.

All pure / CPU; no GPU, no sim. Three layers:
  1. the Kabsch helper in isolation (translation, known rotation, degenerate);
  2. the matrix<->quaternion + compose helpers;
  3. end-to-end through KNNKabschFuser on synthetic sim frames.
"""
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement
from scipy.spatial.transform import Rotation as Rot

from gsfluent.core.fusers.knn_kabsch import (
    KNNKabschFuser,
    _matrices_to_quats_wxyz,
    _quat_mul_wxyz,
    _quats_wxyz_to_matrices,
    _R_ZUP,
    _read_sim_rot_quats,
    _weighted_kabsch,
)


# --- helpers ----------------------------------------------------------------


def _uniform_weights(n: int, k: int) -> np.ndarray:
    return np.full((n, k), 1.0 / k, dtype=np.float64)


def _write_full_3dgs_ply(
    path: Path,
    pts: np.ndarray,
    rest_quats_wxyz: np.ndarray | None = None,
) -> None:
    """Minimal full 3DGS ply at the given (n, 3) positions."""
    n = len(pts)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = pts[:, 0].astype(np.float32)
    verts["y"] = pts[:, 1].astype(np.float32)
    verts["z"] = pts[:, 2].astype(np.float32)
    verts["opacity"] = 0.5
    verts["scale_0"] = verts["scale_1"] = verts["scale_2"] = -1.0
    if rest_quats_wxyz is None:
        verts["rot_0"] = 1.0  # identity
    else:
        verts["rot_0"] = rest_quats_wxyz[:, 0].astype(np.float32)
        verts["rot_1"] = rest_quats_wxyz[:, 1].astype(np.float32)
        verts["rot_2"] = rest_quats_wxyz[:, 2].astype(np.float32)
        verts["rot_3"] = rest_quats_wxyz[:, 3].astype(np.float32)
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def _quats_from_attrs(attrs: np.ndarray) -> np.ndarray:
    return np.stack(
        [attrs["rot_0"], attrs["rot_1"], attrs["rot_2"], attrs["rot_3"]],
        axis=1,
    ).astype(np.float64)


# --- 1. the Kabsch helper in isolation --------------------------------------


def test_kabsch_pure_translation_is_identity() -> None:
    """Neighbours that only translate -> R = I (no spurious rotation)."""
    rng = np.random.default_rng(0)
    rest = rng.uniform(-1, 1, (5, 8, 3))
    cur = rest + np.array([1.0, -2.0, 3.0])  # rigid translation, no rotation
    r = _weighted_kabsch(rest, cur, _uniform_weights(5, 8))
    np.testing.assert_allclose(r, np.broadcast_to(np.eye(3), (5, 3, 3)), atol=1e-9)


def test_kabsch_recovers_known_rotation() -> None:
    """A known rotation applied to the neighbours is recovered within tol."""
    rng = np.random.default_rng(1)
    for axis, deg in [("z", 40.0), ("x", 75.0), ("y", -110.0)]:
        r_true = Rot.from_euler(axis, deg, degrees=True).as_matrix()
        rest = rng.uniform(-1, 1, (1, 8, 3))
        # rotate about the (weighted) centroid, plus an arbitrary translation
        c = rest.mean(axis=1, keepdims=True)
        cur = (r_true @ (rest - c).transpose(0, 2, 1)).transpose(0, 2, 1) + c
        cur = cur + np.array([0.3, -0.2, 0.1])
        r = _weighted_kabsch(rest, cur, _uniform_weights(1, 8))
        np.testing.assert_allclose(r[0], r_true, atol=1e-6)
        # proper rotation, never a reflection
        assert np.isclose(np.linalg.det(r[0]), 1.0, atol=1e-6)


def test_kabsch_uses_weights() -> None:
    """Weights bias the fit: a noisy outlier neighbour with tiny weight barely
    perturbs the rotation recovered from the well-behaved, heavily-weighted
    neighbours; with equal weight it perturbs it much more."""
    r_true = Rot.from_euler("z", 30.0, degrees=True).as_matrix()
    rng = np.random.default_rng(21)
    rest = rng.uniform(-1, 1, (8, 3))
    c = rest.mean(axis=0)
    cur = (r_true @ (rest - c).T).T + c
    # corrupt one neighbour's current position (an outlier that disagrees)
    cur_bad = cur.copy()
    cur_bad[0] += np.array([2.0, -2.0, 1.5])

    w_down = np.array([[0.001] + [0.999 / 7] * 7])   # outlier nearly ignored
    w_equal = np.full((1, 8), 1.0 / 8)               # outlier weighted equally
    r_down = _weighted_kabsch(rest[None], cur_bad[None], w_down)[0]
    r_equal = _weighted_kabsch(rest[None], cur_bad[None], w_equal)[0]

    err_down = np.degrees(Rot.from_matrix(r_down @ r_true.T).magnitude())
    err_equal = np.degrees(Rot.from_matrix(r_equal @ r_true.T).magnitude())
    # down-weighting the outlier keeps us much closer to the true rotation
    assert err_down < err_equal
    assert err_down < 2.0


def test_kabsch_collinear_falls_back_to_identity() -> None:
    """Collinear neighbours (rank-deficient cross-cov) -> identity, finite."""
    line = np.linspace(-1, 1, 8)[None, :, None] * np.array([1.0, 2.0, -0.5])
    r_true = Rot.from_euler("z", 30.0, degrees=True).as_matrix()
    cur = (r_true @ line[0].T).T[None]
    r = _weighted_kabsch(line, cur, _uniform_weights(1, 8))
    assert np.isfinite(r).all()
    np.testing.assert_allclose(r[0], np.eye(3), atol=1e-9)


def test_kabsch_coincident_falls_back_to_identity() -> None:
    """Coincident neighbours (zero cross-cov) -> identity, finite, no NaN."""
    rest = np.zeros((3, 8, 3))
    cur = np.ones((3, 8, 3))  # all neighbours sit on one point
    r = _weighted_kabsch(rest, cur, _uniform_weights(3, 8))
    assert np.isfinite(r).all()
    np.testing.assert_allclose(r, np.broadcast_to(np.eye(3), (3, 3, 3)), atol=1e-9)


def test_kabsch_180_degree_rotation_no_reflection() -> None:
    """A near-180deg rotation must stay a proper rotation (det=+1), not flip
    into a reflection — the det-correction's job."""
    r_true = Rot.from_euler("y", 179.0, degrees=True).as_matrix()
    rng = np.random.default_rng(7)
    rest = rng.uniform(-1, 1, (1, 8, 3))
    c = rest.mean(axis=1, keepdims=True)
    cur = (r_true @ (rest - c).transpose(0, 2, 1)).transpose(0, 2, 1) + c
    r = _weighted_kabsch(rest, cur, _uniform_weights(1, 8))[0]
    assert np.isclose(np.linalg.det(r), 1.0, atol=1e-6)
    np.testing.assert_allclose(r, r_true, atol=1e-5)


# --- 2. matrix<->quaternion + compose helpers -------------------------------


def test_matrices_to_quats_roundtrip() -> None:
    rng = np.random.default_rng(2)
    mats = Rot.random(20, random_state=rng).as_matrix()
    q = _matrices_to_quats_wxyz(mats)  # (20, 4) wxyz
    q_xyzw = np.concatenate([q[:, 1:], q[:, :1]], axis=1)
    back = Rot.from_quat(q_xyzw).as_matrix()
    np.testing.assert_allclose(back, mats, atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(q, axis=1), 1.0, atol=1e-6)


def test_quat_mul_matches_scipy() -> None:
    """_quat_mul_wxyz(qa, qb) == qa applied after qb (left-multiply)."""
    rng = np.random.default_rng(5)
    ra = Rot.random(10, random_state=rng)
    rb = Rot.random(10, random_state=rng)
    qa = ra.as_quat()  # xyzw
    qb = rb.as_quat()
    qa_w = np.concatenate([qa[:, 3:], qa[:, :3]], axis=1)
    qb_w = np.concatenate([qb[:, 3:], qb[:, :3]], axis=1)
    got = _quat_mul_wxyz(qa_w, qb_w)
    got_xyzw = np.concatenate([got[:, 1:], got[:, :1]], axis=1)
    expect = (ra * rb).as_matrix()
    np.testing.assert_allclose(
        Rot.from_quat(got_xyzw).as_matrix(), expect, atol=1e-6
    )


def test_r_zup_constant_matches_position_helper() -> None:
    """_R_ZUP applied as a matrix == rotate_positions_y_up_to_z_up."""
    from gsfluent.core.coord_convert import rotate_positions_y_up_to_z_up

    pts = np.array([[1.0, 2.0, 3.0], [-4.0, 5.0, -6.0]])
    np.testing.assert_allclose((_R_ZUP @ pts.T).T, rotate_positions_y_up_to_z_up(pts))


# --- 3. end-to-end through KNNKabschFuser -----------------------------------


def _rotate_about_centroid(pts: np.ndarray, rmat: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    return ((rmat @ (pts - c).T).T + c).astype(np.float32)


def test_fuse_frame_quaternion_changes_when_cloud_rotates(tmp_path: Path) -> None:
    """End-to-end: when the sim cloud rotates between frames, the per-frame
    output quaternions must change (the headline win — they used to be frozen)."""
    rng = np.random.default_rng(0)
    ref_pts = rng.uniform(0.5, 1.5, (40, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)  # identity rest quats

    sim0 = rng.uniform(0.4, 1.6, (80, 3)).astype(np.float32)
    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0)

    r_true = Rot.from_euler("z", 55.0, degrees=True).as_matrix().astype(np.float32)
    sim_t = _rotate_about_centroid(sim0, r_true)

    out0 = fuser.fuse_frame(corr, sim0)["full_attrs"]
    out_t = fuser.fuse_frame(corr, sim_t)["full_attrs"]
    q0 = _quats_from_attrs(out0)
    qt = _quats_from_attrs(out_t)

    assert np.isfinite(qt).all()
    np.testing.assert_allclose(np.linalg.norm(qt, axis=1), 1.0, atol=1e-5)
    # the headline assertion: quaternions are NOT frozen across rotation frames
    assert not np.allclose(q0, qt, atol=1e-3)
    # and they actually moved by a meaningful amount
    assert np.abs(q0 - qt).mean() > 1e-2


def test_fuse_frame_no_motion_preserves_rest_quaternion(tmp_path: Path) -> None:
    """Frame 0 (sim == frame-0 sim) -> R = identity -> output quats equal the
    stored (zup-rotated) rest quats, up to sign."""
    rng = np.random.default_rng(3)
    ref_pts = rng.uniform(0.5, 1.5, (25, 3)).astype(np.float32)
    rest_q = rng.normal(size=(25, 4))
    rest_q /= np.linalg.norm(rest_q, axis=1, keepdims=True)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts, rest_quats_wxyz=rest_q)

    sim0 = rng.uniform(0.4, 1.6, (70, 3)).astype(np.float32)
    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0)
    rest_stored = fuser._state[id(corr)].rest_quats_wxyz

    out0 = _quats_from_attrs(fuser.fuse_frame(corr, sim0)["full_attrs"])
    dots = np.abs((out0 * rest_stored).sum(axis=1))  # |q . q'| == 1 iff equal up to sign
    np.testing.assert_allclose(dots, 1.0, atol=1e-5)


def test_fuse_frame_pure_translation_preserves_rest_quaternion(tmp_path: Path) -> None:
    """Rigidly translating the whole sim cloud -> no rotation -> rest quats
    preserved (up to sign)."""
    rng = np.random.default_rng(4)
    ref_pts = rng.uniform(0.5, 1.5, (25, 3)).astype(np.float32)
    rest_q = rng.normal(size=(25, 4))
    rest_q /= np.linalg.norm(rest_q, axis=1, keepdims=True)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts, rest_quats_wxyz=rest_q)

    sim0 = rng.uniform(0.4, 1.6, (70, 3)).astype(np.float32)
    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0)
    rest_stored = fuser._state[id(corr)].rest_quats_wxyz

    sim_t = (sim0 + np.array([3.0, -5.0, 2.0], dtype=np.float32)).astype(np.float32)
    out_t = _quats_from_attrs(fuser.fuse_frame(corr, sim_t)["full_attrs"])
    dots = np.abs((out_t * rest_stored).sum(axis=1))
    np.testing.assert_allclose(dots, 1.0, atol=1e-5)


def test_fuse_frame_rotation_amount_tracks_cloud(tmp_path: Path) -> None:
    """A rigidly-rotating cloud rotates each splat by ~the same angle as the
    cloud. The output quaternion is R_world_delta composed onto the (zup-
    rotated) rest pose, so the *relative* rotation output-vs-rest must equal the
    cloud's rotation angle. Because the sim cube frame is conjugated into the
    Z-up world frame, the relative rotation magnitude is invariant — so this
    checks the recovered angle directly."""
    rng = np.random.default_rng(8)
    # tight cloud so the local KNN rotation == the global rigid rotation
    ref_pts = rng.uniform(0.8, 1.2, (60, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)  # identity Y-up rest quats

    sim0 = rng.uniform(0.7, 1.3, (120, 3)).astype(np.float32)
    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0)
    rest_stored = fuser._state[id(corr)].rest_quats_wxyz  # zup-rotated rest pose

    deg = 35.0
    r_true = Rot.from_euler("x", deg, degrees=True).as_matrix().astype(np.float32)
    sim_t = _rotate_about_centroid(sim0, r_true)

    out_t = _quats_from_attrs(fuser.fuse_frame(corr, sim_t)["full_attrs"])

    def _xyzw(q: np.ndarray) -> np.ndarray:
        return np.concatenate([q[:, 1:], q[:, :1]], axis=1)

    # relative rotation: out = delta ∘ rest  ->  delta = out ∘ rest⁻¹
    r_out = Rot.from_quat(_xyzw(out_t))
    r_rest = Rot.from_quat(_xyzw(rest_stored))
    delta_angles = np.degrees((r_out * r_rest.inv()).magnitude())
    assert abs(np.median(delta_angles) - deg) < 5.0


def test_fuse_frame_k1_keeps_quaternion_frozen(tmp_path: Path) -> None:
    """K=1 carries no orientation signal -> the rest quaternion stays frozen
    (graceful: rotation needs >= 2 neighbours to be defined)."""
    rng = np.random.default_rng(9)
    ref_pts = rng.uniform(0.5, 1.5, (15, 3)).astype(np.float32)
    rest_q = rng.normal(size=(15, 4))
    rest_q /= np.linalg.norm(rest_q, axis=1, keepdims=True)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts, rest_quats_wxyz=rest_q)

    sim0 = rng.uniform(0.4, 1.6, (50, 3)).astype(np.float32)
    fuser = KNNKabschFuser(k=1)
    corr = fuser.build_correspondence(ref, sim0)
    rest_stored = fuser._state[id(corr)].rest_quats_wxyz

    sim_t = _rotate_about_centroid(sim0, Rot.from_euler("z", 60, degrees=True).as_matrix().astype(np.float32))
    out_t = _quats_from_attrs(fuser.fuse_frame(corr, sim_t)["full_attrs"])
    np.testing.assert_allclose(out_t, rest_stored, atol=1e-6)


def test_fuse_frame_position_update_unchanged_by_rotation(tmp_path: Path) -> None:
    """Adding rotation must not perturb the existing position skinning."""
    rng = np.random.default_rng(11)
    ref_pts = rng.uniform(0.5, 1.5, (30, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    sim0 = rng.uniform(0.4, 1.6, (60, 3)).astype(np.float32)
    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0)

    sim_t = _rotate_about_centroid(sim0, Rot.from_euler("z", 25, degrees=True).as_matrix().astype(np.float32))
    out = fuser.fuse_frame(corr, sim_t)

    # reproduce the position-only blend independently
    state = fuser._state[id(corr)]
    sim_disp = sim_t - state.sim_xyz_t0_kept
    ref_disp = (state.knn_weights[..., None] * sim_disp[state.knn_idx]).sum(axis=1)
    expect_world = fuser._transform_sim_xyz(
        state.ref_xyz_norm + ref_disp, extent=state.extent, center=state.center
    )
    np.testing.assert_allclose(out["xyz"], expect_world, atol=1e-5)


def test_fuse_sequence_rotation_varies_across_frames(tmp_path: Path) -> None:
    """Smoke end-to-end over a multi-frame sequence dir: as the cloud rotates
    progressively, the written-frame quaternions vary frame to frame."""
    rng = np.random.default_rng(13)
    ref_pts = rng.uniform(0.5, 1.5, (30, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    sim0 = rng.uniform(0.4, 1.6, (60, 3)).astype(np.float32)
    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    angles = [0.0, 20.0, 45.0, 70.0]
    for i, deg in enumerate(angles):
        rmat = Rot.from_euler("z", deg, degrees=True).as_matrix().astype(np.float32)
        pts = _rotate_about_centroid(sim0, rmat) if deg else sim0
        verts = np.zeros(len(pts), dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
        verts["x"], verts["y"], verts["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
        PlyData([PlyElement.describe(verts, "vertex")], text=False).write(
            sim_dir / f"sim_{i:04d}.ply"
        )

    out_dir = tmp_path / "out"
    fuser = KNNKabschFuser(k=8)
    n = fuser.fuse_sequence_dir(ref, sim_dir, out_dir)
    assert n == len(angles)

    quats = []
    for i in range(len(angles)):
        v = PlyData.read(str(out_dir / f"frame_{i:04d}.ply"))["vertex"].data
        quats.append(_quats_from_attrs(v))
    quats = np.stack(quats)  # (frames, n_ref, 4)
    assert np.isfinite(quats).all()
    # frame 0 (no rotation) vs the others: quats must differ, and grow with angle
    drift = [np.abs(quats[i] - quats[0]).mean() for i in range(len(angles))]
    assert drift[0] == pytest.approx(0.0, abs=1e-6)
    assert drift[1] < drift[2] < drift[3]


# --- 4. GPU sim-R path (per-particle polar rotation from the solver) ---------
#
# These pin the new hot path: the sim emits each particle's exact polar
# rotation R = polar(F) per frame (--output_rot); the fuser gathers each
# splat's bound particles' R through the KNN map and composes it onto the rest
# quaternion — no CPU Kabsch SVD. We synthesize the sim's per-particle R the
# way the GPU would: for a rigid cloud rotation, every particle's polar R IS
# that rotation, so the sim-R path and the CPU-Kabsch fallback must agree.


def _quats_wxyz_from_mats(mats: np.ndarray) -> np.ndarray:
    return _matrices_to_quats_wxyz(mats)


def test_sim_rot_field_roundtrip() -> None:
    """_read_sim_rot_quats pulls rot_w..rot_z; absent -> None."""
    dt_with = [("x", "f4"), ("y", "f4"), ("z", "f4"),
               ("rot_w", "f4"), ("rot_x", "f4"), ("rot_y", "f4"), ("rot_z", "f4")]
    v = np.zeros(5, dtype=dt_with)
    v["rot_w"] = 1.0
    q = _read_sim_rot_quats(v)
    assert q is not None and q.shape == (5, 4)
    np.testing.assert_allclose(q[:, 0], 1.0)

    v_no = np.zeros(5, dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    assert _read_sim_rot_quats(v_no) is None


def test_quats_to_matrices_roundtrip() -> None:
    rng = np.random.default_rng(31)
    mats = Rot.random(20, random_state=rng).as_matrix()
    q = _matrices_to_quats_wxyz(mats)
    back = _quats_wxyz_to_matrices(q)
    np.testing.assert_allclose(back, mats, atol=1e-9)


def test_sim_r_path_engages_and_quaternion_varies(tmp_path: Path) -> None:
    """When frame-0 + per-frame R are supplied, fuse_frame uses the GPU sim-R
    path (sim_R0_mats set) and the output quaternions follow the cloud."""
    rng = np.random.default_rng(40)
    ref_pts = rng.uniform(0.5, 1.5, (40, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    sim0 = rng.uniform(0.4, 1.6, (80, 3)).astype(np.float32)
    rot0 = np.tile([1.0, 0.0, 0.0, 0.0], (80, 1))  # frame 0: identity R

    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0, first_frame_rot_quats=rot0)
    assert fuser._state[id(corr)].sim_R0_mats is not None

    r_true = Rot.from_euler("z", 50.0, degrees=True)
    sim_t = _rotate_about_centroid(sim0, r_true.as_matrix().astype(np.float32))
    # every particle's polar R is the rigid rotation
    rot_t = np.tile(
        np.concatenate([r_true.as_quat()[3:], r_true.as_quat()[:3]]), (80, 1)
    )

    out0 = _quats_from_attrs(fuser.fuse_frame(corr, sim0, rot_quats=rot0)["full_attrs"])
    out_t = _quats_from_attrs(fuser.fuse_frame(corr, sim_t, rot_quats=rot_t)["full_attrs"])
    assert np.isfinite(out_t).all()
    np.testing.assert_allclose(np.linalg.norm(out_t, axis=1), 1.0, atol=1e-5)
    assert not np.allclose(out0, out_t, atol=1e-3)


def test_sim_r_matches_cpu_kabsch_on_rigid_rotation(tmp_path: Path) -> None:
    """Cross-validation (the task's sanity check): on a rigidly-rotating cloud
    the GPU sim-R path and the CPU-Kabsch fallback must produce the SAME splat
    quaternions (up to sign) — both recover the same rigid rotation, one from
    per-particle polar R, the other from neighbour displacements."""
    rng = np.random.default_rng(41)
    ref_pts = rng.uniform(0.6, 1.4, (50, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    sim0 = rng.uniform(0.5, 1.5, (100, 3)).astype(np.float32)
    rot0 = np.tile([1.0, 0.0, 0.0, 0.0], (100, 1))

    for axis, deg in [("z", 40.0), ("x", 65.0), ("y", -30.0)]:
        r_true = Rot.from_euler(axis, deg, degrees=True)
        sim_t = _rotate_about_centroid(sim0, r_true.as_matrix().astype(np.float32))
        rot_t = np.tile(
            np.concatenate([r_true.as_quat()[3:], r_true.as_quat()[:3]]), (100, 1)
        )

        # CPU-Kabsch fallback (no rot supplied)
        f_cpu = KNNKabschFuser(k=8)
        c_cpu = f_cpu.build_correspondence(ref, sim0)
        q_cpu = _quats_from_attrs(f_cpu.fuse_frame(c_cpu, sim_t)["full_attrs"])

        # GPU sim-R path (rot supplied)
        f_gpu = KNNKabschFuser(k=8)
        c_gpu = f_gpu.build_correspondence(ref, sim0, first_frame_rot_quats=rot0)
        q_gpu = _quats_from_attrs(
            f_gpu.fuse_frame(c_gpu, sim_t, rot_quats=rot_t)["full_attrs"]
        )

        dots = np.abs((q_cpu * q_gpu).sum(axis=1))  # 1.0 iff equal up to sign
        np.testing.assert_allclose(dots, 1.0, atol=1e-3)


def test_sim_r_no_motion_preserves_rest_quaternion(tmp_path: Path) -> None:
    """Frame-0 R == per-frame R -> delta identity -> rest quats preserved."""
    rng = np.random.default_rng(42)
    ref_pts = rng.uniform(0.5, 1.5, (25, 3)).astype(np.float32)
    rest_q = rng.normal(size=(25, 4))
    rest_q /= np.linalg.norm(rest_q, axis=1, keepdims=True)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts, rest_quats_wxyz=rest_q)

    sim0 = rng.uniform(0.4, 1.6, (60, 3)).astype(np.float32)
    # non-identity but constant per-particle R (frame 0 == frame t)
    base = Rot.random(60, random_state=rng)
    rot0 = np.concatenate([base.as_quat()[:, 3:], base.as_quat()[:, :3]], axis=1)

    fuser = KNNKabschFuser(k=8)
    corr = fuser.build_correspondence(ref, sim0, first_frame_rot_quats=rot0)
    rest_stored = fuser._state[id(corr)].rest_quats_wxyz

    out0 = _quats_from_attrs(
        fuser.fuse_frame(corr, sim0, rot_quats=rot0)["full_attrs"]
    )
    dots = np.abs((out0 * rest_stored).sum(axis=1))
    np.testing.assert_allclose(dots, 1.0, atol=1e-5)


def test_sim_r_sequence_dir_autodetects_rot_fields(tmp_path: Path) -> None:
    """End-to-end through fuse_sequence_dir: sim plys carrying rot_* columns
    drive the GPU sim-R path; quaternions vary and grow with rotation angle."""
    rng = np.random.default_rng(43)
    ref_pts = rng.uniform(0.5, 1.5, (30, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    sim0 = rng.uniform(0.4, 1.6, (60, 3)).astype(np.float32)
    sim_dir = tmp_path / "sim"
    sim_dir.mkdir()
    angles = [0.0, 20.0, 45.0, 70.0]
    rot_dt = [("x", "f4"), ("y", "f4"), ("z", "f4"),
              ("rot_w", "f4"), ("rot_x", "f4"), ("rot_y", "f4"), ("rot_z", "f4")]
    for i, deg in enumerate(angles):
        r = Rot.from_euler("z", deg, degrees=True)
        pts = _rotate_about_centroid(sim0, r.as_matrix().astype(np.float32)) if deg else sim0
        q = np.concatenate([r.as_quat()[3:], r.as_quat()[:3]])  # wxyz
        verts = np.zeros(len(pts), dtype=rot_dt)
        verts["x"], verts["y"], verts["z"] = pts[:, 0], pts[:, 1], pts[:, 2]
        verts["rot_w"], verts["rot_x"], verts["rot_y"], verts["rot_z"] = q
        PlyData([PlyElement.describe(verts, "vertex")], text=False).write(
            sim_dir / f"sim_{i:04d}.ply"
        )

    out_dir = tmp_path / "out"
    fuser = KNNKabschFuser(k=8)
    n = fuser.fuse_sequence_dir(ref, sim_dir, out_dir)
    assert n == len(angles)

    quats = []
    for i in range(len(angles)):
        v = PlyData.read(str(out_dir / f"frame_{i:04d}.ply"))["vertex"].data
        quats.append(_quats_from_attrs(v))
    quats = np.stack(quats)
    assert np.isfinite(quats).all()
    drift = [np.abs(quats[i] - quats[0]).mean() for i in range(len(angles))]
    assert drift[0] == pytest.approx(0.0, abs=1e-6)
    assert drift[1] < drift[2] < drift[3]
