"""KNNKabschFuser — Fuser Protocol impl. K-NN inverse-distance skinning of
reference 3DGS attributes onto per-frame sim particle positions, **plus**
a per-splat local rigid rotation recovered from the same K neighbors via
Kabsch / orthogonal Procrustes (the rotation the class name always promised).

Per-frame deformation, per reference splat:
  - POSITION: inverse-distance-weighted blend of its K nearest sim particles'
    displacements (linear-blend skinning, unchanged from prior behaviour).
  - ROTATION: the optimal local rigid rotation R mapping the REST neighbour
    set (frame-0 sim positions) -> the CURRENT neighbour set (frame-t), found
    by weighted Kabsch SVD over the same K neighbours with the same weights.
    R is composed onto the splat's rest quaternion so the anisotropic lobe
    rotates with the material. Storing this needs ZERO codec/schema change —
    the v1 .gsq already carries a per-frame quaternion per splat.

R is solved in the *normalized cube* frame (the frame positions/displacements
live in), then conjugated into the Z-up world frame the stored quaternions use
(R_world = R_zup R_cube R_zupᵀ) before composing.

Degenerate neighbourhoods (collinear / coincident -> rank-deficient
cross-covariance) fall back to identity rotation; output is always finite.

Scope: this Protocol impl covers the **production defaults** of the prior
script — K-NN skinning (K>=1), source-scale output, Y-up to Z-up rotation,
center-at-origin, and now Kabsch per-splat rotation. The script's other special
paths (cov-field particle_F mode, watch mode, subsample, min_opacity opacity
filter) remain out of scope for the Protocol contract this phase enshrines.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree

from gsfluent.core.coord_convert import (
    rotate_normals_y_up_to_z_up as _rotate_norm,
)
from gsfluent.core.coord_convert import (
    rotate_positions_y_up_to_z_up as _rotate_pos,
)
from gsfluent.core.coord_convert import (
    rotate_quaternions_y_up_to_z_up as _rotate_quat,
)
from gsfluent.protocols.fuse import (
    Correspondence,
    FuseDegenerateClusterError,
    FuseError,
    FuseNonFiniteInputError,
    ParticleFrame,
    SplatFrame,
)

# ---- math helpers copied verbatim from tools/fuse_to_full_ply.py -----------


def _norm_xyz_to_origin_cube(
    xyz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Normalize reference xyz: longest axis -> 1.0, center -> (1,1,1).

    Returns (normalized_xyz, center, extent) — the same convention the sim's
    transform2origin uses on the reference data.
    """
    aabb_min = xyz.min(0)
    aabb_max = xyz.max(0)
    center = (aabb_min + aabb_max) / 2.0
    extent = float((aabb_max - aabb_min).max())
    if extent == 0.0:
        raise FuseError(f"reference ply has zero-extent bbox: {aabb_min}..{aabb_max}")
    normed = ((xyz - center) / extent + 1.0).astype(np.float32)
    return normed, center, extent


# ---- Kabsch rotation + quaternion helpers ----------------------------------

# Y-up -> Z-up basis change as a rotation matrix: (x, y, z) -> (x, -z, y).
# This is exactly rotate_positions_y_up_to_z_up expressed as a matrix; it is
# used to conjugate a cube-frame rotation into the stored Z-up world frame.
_R_ZUP = np.array(
    [[1.0, 0.0, 0.0],
     [0.0, 0.0, -1.0],
     [0.0, 1.0, 0.0]],
    dtype=np.float64,
)


def _weighted_kabsch(
    p_rest: np.ndarray,
    p_cur: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Batched weighted Kabsch / orthogonal Procrustes.

    For each splat, find the rigid rotation R (proper, det=+1) that best maps
    its REST neighbour set onto its CURRENT neighbour set, in a least-squares
    sense weighted by `weights`:

        c_rest = Σ wₖ p_restₖ            (weighted centroid, Σ wₖ = 1)
        c_cur  = Σ wₖ p_curₖ
        H      = Σ wₖ (p_restₖ − c_rest)(p_curₖ − c_cur)ᵀ      (3×3)
        H      = U Σ Vᵀ                                         (SVD)
        d      = sign(det(V Uᵀ))                                (reflection fix)
        R      = V · diag(1, 1, d) · Uᵀ

    With this H (rest on the left, current on the right), R rotates a *rest*
    vector into the *current* frame: (p_cur − c_cur) ≈ R (p_rest − c_rest).

    Args:
        p_rest:  (n, K, 3) rest neighbour positions.
        p_cur:   (n, K, 3) current neighbour positions.
        weights: (n, K) inverse-distance weights, rows summing to ~1.

    Returns:
        (n, 3, 3) rotation matrices. Degenerate neighbourhoods (collinear /
        coincident -> rank-deficient H) fall back to identity. Output is always
        finite.
    """
    p_rest = p_rest.astype(np.float64, copy=False)
    p_cur = p_cur.astype(np.float64, copy=False)
    w = weights.astype(np.float64, copy=False)[..., None]  # (n, K, 1)

    c_rest = (w * p_rest).sum(axis=1, keepdims=True)  # (n, 1, 3)
    c_cur = (w * p_cur).sum(axis=1, keepdims=True)    # (n, 1, 3)
    q_rest = p_rest - c_rest                           # (n, K, 3)
    q_cur = p_cur - c_cur

    # H = Σ wₖ q_restₖ ⊗ q_curₖ  -> (n, 3, 3).
    wq_rest = w * q_rest                                # (n, K, 3)
    h = np.einsum("nki,nkj->nij", wq_rest, q_cur)      # (n, 3, 3)

    n = h.shape[0]
    out = np.broadcast_to(np.eye(3), (n, 3, 3)).copy()  # identity fallback

    # SVD can fail to converge on pathological (e.g. all-zero) matrices; guard.
    # Rows whose cross-covariance carries essentially no signal (coincident
    # neighbours -> H ≈ 0) keep the identity fallback.
    fro = np.sqrt((h * h).reshape(n, -1).sum(axis=1))   # Frobenius norm per row
    solvable = (fro > 1e-12) & np.isfinite(fro)
    if not solvable.any():
        return out

    idx = np.flatnonzero(solvable)
    try:
        u, s, vt = np.linalg.svd(h[idx])               # H = U S Vᵀ, σ descending
    except np.linalg.LinAlgError:
        return out

    v = np.transpose(vt, (0, 2, 1))                    # V
    ut = np.transpose(u, (0, 2, 1))                    # Uᵀ
    det = np.linalg.det(np.matmul(v, ut))              # det(V Uᵀ)
    d = np.ones((len(idx), 3))
    d[:, 2] = np.sign(det)
    # diag(1,1,d) folds into V's last column: V · diag · Uᵀ.
    v_corrected = v * d[:, None, :]
    r = np.matmul(v_corrected, ut)                     # (m, 3, 3)

    # Reject rank-deficient neighbourhoods: a well-posed Kabsch needs the top
    # two singular values to carry real signal (>= 3 non-collinear points).
    # Use a *scale-invariant* condition — σ₂ relative to σ₁ — so genuinely
    # tiny-but-valid neighbourhoods (dense clusters) are still solved while
    # collinear sets (σ₂ ≈ 0) fall back to identity. Reflections (σ flipped by
    # the det-correction) and any non-finite SVD output also fall back.
    s0 = np.maximum(s[:, 0], 1e-300)
    well_posed = (s[:, 1] / s0) > 1e-6
    finite = np.isfinite(r).reshape(len(idx), -1).all(axis=1)
    keep = well_posed & finite
    out[idx[keep]] = r[keep]
    return out


def _matrices_to_quats_wxyz(rmats: np.ndarray) -> np.ndarray:
    """Convert (n, 3, 3) rotation matrices to (n, 4) quaternions in (w,x,y,z)
    order, matching the 3DGS .ply rot_0..rot_3 convention. Numerically stable
    (Shepperd's method); assumes proper rotations (det≈+1)."""
    r = rmats.astype(np.float64, copy=False)
    n = r.shape[0]
    m00, m11, m22 = r[:, 0, 0], r[:, 1, 1], r[:, 2, 2]
    trace = m00 + m11 + m22

    qw = np.empty(n)
    qx = np.empty(n)
    qy = np.empty(n)
    qz = np.empty(n)

    # Four cases by largest diagonal term to avoid catastrophic cancellation.
    c0 = trace > 0.0
    c1 = (~c0) & (m00 >= m11) & (m00 >= m22)
    c2 = (~c0) & (~c1) & (m11 >= m22)
    c3 = ~c0 & ~c1 & ~c2

    if c0.any():
        s = np.sqrt(trace[c0] + 1.0) * 2.0
        qw[c0] = 0.25 * s
        qx[c0] = (r[c0, 2, 1] - r[c0, 1, 2]) / s
        qy[c0] = (r[c0, 0, 2] - r[c0, 2, 0]) / s
        qz[c0] = (r[c0, 1, 0] - r[c0, 0, 1]) / s
    if c1.any():
        s = np.sqrt(1.0 + m00[c1] - m11[c1] - m22[c1]) * 2.0
        qw[c1] = (r[c1, 2, 1] - r[c1, 1, 2]) / s
        qx[c1] = 0.25 * s
        qy[c1] = (r[c1, 0, 1] + r[c1, 1, 0]) / s
        qz[c1] = (r[c1, 0, 2] + r[c1, 2, 0]) / s
    if c2.any():
        s = np.sqrt(1.0 + m11[c2] - m00[c2] - m22[c2]) * 2.0
        qw[c2] = (r[c2, 0, 2] - r[c2, 2, 0]) / s
        qx[c2] = (r[c2, 0, 1] + r[c2, 1, 0]) / s
        qy[c2] = 0.25 * s
        qz[c2] = (r[c2, 1, 2] + r[c2, 2, 1]) / s
    if c3.any():
        s = np.sqrt(1.0 + m22[c3] - m00[c3] - m11[c3]) * 2.0
        qw[c3] = (r[c3, 1, 0] - r[c3, 0, 1]) / s
        qx[c3] = (r[c3, 0, 2] + r[c3, 2, 0]) / s
        qy[c3] = (r[c3, 1, 2] + r[c3, 2, 1]) / s
        qz[c3] = 0.25 * s

    q = np.stack([qw, qx, qy, qz], axis=1)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    return q


def _quat_mul_wxyz(qa: np.ndarray, qb: np.ndarray) -> np.ndarray:
    """Hamilton product q = qa * qb for (n, 4) quaternions in (w,x,y,z) order.

    Composing qa onto qb means qa is applied *after* qb (left-multiply), the
    same convention rotate_quaternions_y_up_to_z_up uses to fold the basis
    rotation onto each rest quaternion.
    """
    wa, xa, ya, za = qa[:, 0], qa[:, 1], qa[:, 2], qa[:, 3]
    wb, xb, yb, zb = qb[:, 0], qb[:, 1], qb[:, 2], qb[:, 3]
    return np.stack([
        wa * wb - xa * xb - ya * yb - za * zb,
        wa * xb + xa * wb + ya * zb - za * yb,
        wa * yb - xa * zb + ya * wb + za * xb,
        wa * zb + xa * yb - ya * xb + za * wb,
    ], axis=1)


# ---- KNNKabschFuser class --------------------------------------------------


@dataclass(frozen=True)
class _KNNCorrespondence:
    """Private state stashed inside Correspondence.extent for fuse_frame's reuse.

    Correspondence is a frozen dataclass with fixed fields per the Protocol;
    we encode our extra state on the side. fuse_frame retrieves it via the
    instance dict on the fuser (keyed by Correspondence id).
    """
    ref_xyz_norm: np.ndarray
    center: np.ndarray
    extent: float
    knn_idx: np.ndarray      # (n_ref, K)
    knn_weights: np.ndarray  # (n_ref, K)
    sim_xyz_t0_kept: np.ndarray  # (n_kept, 3) — frame-0 sim particles (cube frame)
    full_attrs: np.ndarray   # FULL reference attr array, post-zup/coord transforms
    rest_quats_wxyz: np.ndarray | None  # (n_ref, 4) rest quats (Z-up world frame),
    #                                     None when the ref ply has no rot fields


class KNNKabschFuser:
    """Fuser Protocol impl using inverse-distance K-NN skinning + Kabsch.

    Construction:
        fuser = KNNKabschFuser(k=8)

    The K parameter controls how many sim particles weight each reference splat's
    displacement (higher K = smoother, more diffusive; K=8 is the production default
    per the spec). All other parameters use the prior script's production defaults:
    Y-up -> Z-up rotation, source-scale output, centered at origin.
    """

    def __init__(self, k: int = 8) -> None:
        if k < 1:
            raise ValueError(f"k must be >= 1; got {k}")
        self.k = k
        # Maps id(Correspondence) -> _KNNCorrespondence side-state. The
        # Protocol's Correspondence is a public frozen dataclass; we keep the
        # K-NN map + reference attrs here to avoid leaking large numpy arrays
        # through the public type.
        self._state: dict[int, _KNNCorrespondence] = {}

    def build_correspondence(
        self,
        reference_ply_path: Path,
        first_frame_particles: ParticleFrame,
    ) -> Correspondence:
        """Build the K-NN reference->particle mapping. One-shot per sequence."""
        first_frame_particles = np.asarray(first_frame_particles, dtype=np.float32)
        if first_frame_particles.ndim != 2 or first_frame_particles.shape[1] != 3:
            raise FuseError(
                f"first_frame_particles must be (N, 3); got shape "
                f"{first_frame_particles.shape}"
            )
        if not np.isfinite(first_frame_particles).all():
            raise FuseNonFiniteInputError("first_frame_particles contains NaN/Inf")

        ref_ply = PlyData.read(str(reference_ply_path))
        ref_v = ref_ply["vertex"].data
        ref_xyz_raw = np.stack(
            [ref_v["x"], ref_v["y"], ref_v["z"]], axis=1,
        ).astype(np.float32)
        ref_xyz_norm, center, extent = _norm_xyz_to_origin_cube(ref_xyz_raw)

        # K-NN: for each REF splat, find K nearest SIM particles at frame 0.
        sim_tree = cKDTree(first_frame_particles)
        effective_k = min(self.k, len(first_frame_particles))
        if effective_k < 1:
            raise FuseError("first_frame_particles is empty")
        dists, knn_idx = sim_tree.query(ref_xyz_norm, k=effective_k, workers=-1)
        if effective_k == 1:
            dists = dists[:, None]
            knn_idx = knn_idx[:, None]
        # Detect totally-degenerate K-NN: all-zero distances mean every
        # sim particle coincides — Kabsch can't solve any rotation.
        if (dists == 0.0).all():
            raise FuseDegenerateClusterError(
                "all K-NN distances are zero; sim particles coincide"
            )
        inv_d = 1.0 / (dists.astype(np.float32) + 1e-6)
        knn_weights = (inv_d / inv_d.sum(axis=1, keepdims=True)).astype(np.float32)

        # Build the FULL reference attribute array with zup rotation +
        # rest-position bake (matches the prior script's production defaults).
        out_dtype = ref_v.dtype
        full_attrs = np.empty(len(ref_v), dtype=out_dtype)
        for field in out_dtype.names:
            full_attrs[field] = ref_v[field]

        # Zup rotation on rotation quats + normals.
        rest_quats_wxyz: np.ndarray | None = None
        if all(k in full_attrs.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
            q = np.stack([
                full_attrs["rot_0"], full_attrs["rot_1"],
                full_attrs["rot_2"], full_attrs["rot_3"],
            ], axis=1).astype(np.float32)
            new_q = _rotate_quat(q)
            full_attrs["rot_0"] = new_q[:, 0]
            full_attrs["rot_1"] = new_q[:, 1]
            full_attrs["rot_2"] = new_q[:, 2]
            full_attrs["rot_3"] = new_q[:, 3]
            # Keep the rest quats (Z-up world frame) to compose per-frame
            # Kabsch rotation onto in fuse_frame.
            rest_quats_wxyz = new_q.astype(np.float64)
        if all(k in full_attrs.dtype.names for k in ("nx", "ny", "nz")):
            n = np.stack([
                full_attrs["nx"], full_attrs["ny"], full_attrs["nz"],
            ], axis=1).astype(np.float32)
            new_n = _rotate_norm(n)
            full_attrs["nx"] = new_n[:, 0]
            full_attrs["ny"] = new_n[:, 1]
            full_attrs["nz"] = new_n[:, 2]

        # Bake rest positions in source-scale + zup + centered-at-origin frame.
        rest_xyz = self._transform_sim_xyz(
            ref_xyz_norm, extent=extent, center=center,
        )
        full_attrs["x"] = rest_xyz[:, 0]
        full_attrs["y"] = rest_xyz[:, 1]
        full_attrs["z"] = rest_xyz[:, 2]

        corr = Correspondence(
            reference_ply_path=reference_ply_path,
            indices=tuple(int(i) for i in range(len(ref_v))),
            extent=extent,
        )
        self._state[id(corr)] = _KNNCorrespondence(
            ref_xyz_norm=ref_xyz_norm,
            center=center,
            extent=extent,
            knn_idx=knn_idx,
            knn_weights=knn_weights,
            sim_xyz_t0_kept=first_frame_particles,
            full_attrs=full_attrs,
            rest_quats_wxyz=rest_quats_wxyz,
        )
        return corr

    def fuse_frame(
        self,
        correspondence: Correspondence,
        particle_frame: ParticleFrame,
    ) -> SplatFrame:
        """K-NN-skin per-frame sim displacement onto every reference splat."""
        state = self._state.get(id(correspondence))
        if state is None:
            raise FuseError(
                f"fuse_frame called with a Correspondence not produced by "
                f"this fuser instance (id={id(correspondence)})"
            )
        particle_frame = np.asarray(particle_frame, dtype=np.float32)
        if particle_frame.ndim != 2 or particle_frame.shape[1] != 3:
            raise FuseError(
                f"particle_frame must be (N, 3); got shape {particle_frame.shape}"
            )
        if not np.isfinite(particle_frame).all():
            raise FuseNonFiniteInputError("particle_frame contains NaN/Inf")
        if particle_frame.shape[0] != state.sim_xyz_t0_kept.shape[0]:
            raise FuseError(
                f"particle_frame has {particle_frame.shape[0]} particles; "
                f"expected {state.sim_xyz_t0_kept.shape[0]} (from frame 0)"
            )

        # --- POSITION: inverse-distance K-NN displacement blend (unchanged). --
        sim_disp = particle_frame - state.sim_xyz_t0_kept              # (n_kept, 3)
        neighbors = sim_disp[state.knn_idx]                            # (n_ref, K, 3)
        ref_disp = (state.knn_weights[..., None] * neighbors).sum(axis=1)
        ref_xyz_displaced = state.ref_xyz_norm + ref_disp              # (n_ref, 3)
        out_xyz_world = self._transform_sim_xyz(
            ref_xyz_displaced, extent=state.extent, center=state.center,
        )

        out = state.full_attrs.copy()
        out["x"] = out_xyz_world[:, 0]
        out["y"] = out_xyz_world[:, 1]
        out["z"] = out_xyz_world[:, 2]

        # --- ROTATION: per-splat local rigid rotation via weighted Kabsch. ----
        # Solve R in the cube frame (rest neighbours -> current neighbours),
        # conjugate into the Z-up world frame, and compose onto the rest quat.
        # The K=1 case carries no orientation signal -> identity (frozen quat),
        # which is the pre-existing behaviour.
        if state.rest_quats_wxyz is not None and state.knn_idx.shape[1] >= 2:
            p_rest = state.sim_xyz_t0_kept[state.knn_idx]              # (n_ref, K, 3)
            p_cur = particle_frame[state.knn_idx]                     # (n_ref, K, 3)
            r_cube = _weighted_kabsch(p_rest, p_cur, state.knn_weights)  # (n_ref,3,3)
            # Cube-frame rotation -> Z-up world frame: R_world = R_zup R R_zupᵀ.
            r_world = _R_ZUP @ r_cube @ _R_ZUP.T                      # (n_ref, 3, 3)
            q_delta = _matrices_to_quats_wxyz(r_world)               # (n_ref, 4)
            q_new = _quat_mul_wxyz(q_delta, state.rest_quats_wxyz)   # (n_ref, 4)
            q_new /= np.linalg.norm(q_new, axis=1, keepdims=True)
            out["rot_0"] = q_new[:, 0].astype(np.float32)
            out["rot_1"] = q_new[:, 1].astype(np.float32)
            out["rot_2"] = q_new[:, 2].astype(np.float32)
            out["rot_3"] = q_new[:, 3].astype(np.float32)

        return {
            "xyz": out_xyz_world,
            "full_attrs": out,
            "n_ref": len(state.full_attrs),
        }

    # ---- convenience entry-point for the CLI wrapper -----------------------

    def fuse_sequence_dir(
        self,
        reference_ply_path: Path,
        sim_dir: Path,
        out_dir: Path,
    ) -> int:
        """Drive the per-frame loop, writing fused frame_*.ply atomically.

        Used by the slim tools/fuse_to_full_ply.py CLI wrapper and by the
        Phase 2 smoke test. Returns the number of frames written.
        """
        import re
        sim_re = re.compile(r"sim_(\d+)\.ply$")
        sim_plys = sorted(sim_dir.glob("sim_*.ply"))
        if not sim_plys:
            raise FuseError(f"no sim_*.ply in {sim_dir}")

        first_data = PlyData.read(str(sim_plys[0]))["vertex"].data
        sim_xyz_t0 = np.stack(
            [first_data["x"], first_data["y"], first_data["z"]], axis=1,
        ).astype(np.float32)

        corr = self.build_correspondence(reference_ply_path, sim_xyz_t0)

        out_dir.mkdir(parents=True, exist_ok=True)
        n_written = 0
        for sp in sim_plys:
            m = sim_re.search(str(sp))
            if m is None:
                continue
            idx = int(m.group(1))
            v = PlyData.read(str(sp))["vertex"].data
            sim_xyz = np.stack(
                [v["x"], v["y"], v["z"]], axis=1,
            ).astype(np.float32)
            try:
                result = self.fuse_frame(corr, sim_xyz)
            except FuseNonFiniteInputError:
                # Skip frames with non-finite sim positions; codec sanitize
                # would forward-fill anyway, but the .ply layer can't carry
                # NaN cleanly to downstream consumers.
                continue

            out_arr = result["full_attrs"]
            out_path = out_dir / f"frame_{idx:04d}.ply"
            tmp_path = Path(str(out_path) + ".tmp")
            PlyData(
                [PlyElement.describe(out_arr, "vertex")], text=False,
            ).write(tmp_path)
            os.replace(str(tmp_path), str(out_path))
            n_written += 1

        return n_written

    # ---- private helpers ---------------------------------------------------

    @staticmethod
    def _transform_sim_xyz(
        sim_xyz: np.ndarray,
        *,
        extent: float,
        center: np.ndarray,
    ) -> np.ndarray:
        """Production defaults: un-normalize back to source-world scale,
        center at origin, then Y-up -> Z-up rotation."""
        sx = sim_xyz[:, 0].astype(np.float32, copy=True)
        sy = sim_xyz[:, 1].astype(np.float32, copy=True)
        sz = sim_xyz[:, 2].astype(np.float32, copy=True)
        # Un-normalize: undo `(x - center) / extent + 1.0` from build_correspondence.
        sx = (sx - 1.0) * extent + center[0]
        sy = (sy - 1.0) * extent + center[1]
        sz = (sz - 1.0) * extent + center[2]
        # Center at origin.
        sx -= center[0]
        sy -= center[1]
        sz -= center[2]
        stacked = np.stack([sx, sy, sz], axis=1)
        # Y-up -> Z-up.
        return _rotate_pos(stacked)
