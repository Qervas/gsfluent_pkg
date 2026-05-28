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


# ---- fracture-aware re-binding (Phase 1) -----------------------------------
#
# The frozen frame-0 K-NN binding makes a splat that straddles a crack average
# two diverging motions -> it stretches across the gap (the demolition "ghost
# web"). Phase 1 detects fracture from POSITIONS ALONE -- a splat's bound
# neighbours flying apart -- and re-partitions that splat's correspondence to a
# single coherent side (hard 1-NN snap). It re-binds the CORRESPONDENCE, so the
# position blend AND the rotation gather both follow the new sided binding.
# Pure numpy, CPU, no codec/sim/GPU change.

# Fracture detection + latch tunables (sane defaults per the proposal §2.1/§2.4).
# A splat fractures when the MAX relative pairwise stretch of its bound
# neighbours, dt/d0, exceeds TAU_STRETCH. Hysteresis: it must stay over the
# threshold for FRACTURE_PATIENCE consecutive frames before it latches, which
# rejects a single noisy FLIP frame and prevents flip-flop. Once latched, the
# decision is MONOTONE -- kept for the rest of the sequence (cracks don't heal
# in these recipes), which also keeps per-splat behaviour temporally coherent
# for the codec's delta scheme.
TAU_STRETCH: float = 1.8
FRACTURE_PATIENCE: int = 2
# Pairs whose rest distance is below this (cube-frame units) are ignored in the
# stretch test: a near-coincident neighbour pair has a tiny, noisy denominator
# that would manufacture spurious giant ratios. The cube frame normalizes the
# longest axis to 1.0, so 1e-4 is ~0.01% of the body extent.
_MIN_REST_PAIR_DIST: float = 1e-4


def _pairwise_rest_dists(p_rest: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rest pairwise distances over each splat's K bound neighbours.

    Args:
        p_rest: (n, K, 3) frame-0 neighbour positions (cube frame).

    Returns:
        (d0, pair_mask):
          d0        (n, P) rest distance for each of the P = K(K-1)/2 unique
                    neighbour pairs (upper-triangle order from np.triu_indices).
          pair_mask (P,)   per-pair validity (rest distance >= _MIN_REST_PAIR_DIST,
                    broadcast over splats inside the caller). Returned per-pair
                    here as (n, P) after the >= test for direct use.
    """
    n, k, _ = p_rest.shape
    iu, ju = np.triu_indices(k, k=1)               # (P,), (P,)
    diff = p_rest[:, iu, :] - p_rest[:, ju, :]     # (n, P, 3)
    d0 = np.linalg.norm(diff, axis=2)              # (n, P)
    valid = d0 >= _MIN_REST_PAIR_DIST              # (n, P)
    return d0.astype(np.float64), valid


def _max_pairwise_stretch(
    p_cur: np.ndarray,
    d0: np.ndarray,
    pair_valid: np.ndarray,
    pair_iu: np.ndarray,
    pair_ju: np.ndarray,
) -> np.ndarray:
    """Per-splat max relative stretch dt/d0 over its valid neighbour pairs.

    A splat is straddling a crack when two of its bound neighbours fly apart:
    under elastic/plastic deformation neighbours stay roughly equidistant
    (stretch ~ 1); a crack running between two neighbours sends dt/d0 >> 1.

    Args:
        p_cur:      (n, K, 3) current neighbour positions (cube frame).
        d0:         (n, P)    rest pairwise distances (from _pairwise_rest_dists).
        pair_valid: (n, P)    per-pair validity mask (rest distance not tiny).
        pair_iu/ju: (P,)      the np.triu_indices that produced d0.

    Returns:
        (n,) the max stretch per splat; pairs masked out (or no valid pairs)
        contribute 0.0 so they never trip the threshold.
    """
    diff = p_cur[:, pair_iu, :] - p_cur[:, pair_ju, :]   # (n, P, 3)
    dt = np.linalg.norm(diff, axis=2)                    # (n, P)
    ratio = np.where(pair_valid, dt / np.maximum(d0, 1e-12), 0.0)
    return ratio.max(axis=1)                             # (n,)


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


# ---- sim-R (GPU polar rotation) helpers ------------------------------------


def _quats_wxyz_to_matrices(q: np.ndarray) -> np.ndarray:
    """Convert (n, 4) unit quaternions in (w,x,y,z) order to (n, 3, 3) rotation
    matrices. Inverse of _matrices_to_quats_wxyz; used to turn the sim's
    per-particle polar-rotation quaternions back into matrices so they compose
    in the same cube-frame -> Z-up conjugation path the CPU Kabsch uses."""
    q = np.asarray(q, dtype=np.float64)
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-300)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    n = q.shape[0]
    m = np.empty((n, 3, 3), dtype=np.float64)
    m[:, 0, 0] = 1.0 - 2.0 * (y * y + z * z)
    m[:, 0, 1] = 2.0 * (x * y - z * w)
    m[:, 0, 2] = 2.0 * (x * z + y * w)
    m[:, 1, 0] = 2.0 * (x * y + z * w)
    m[:, 1, 1] = 1.0 - 2.0 * (x * x + z * z)
    m[:, 1, 2] = 2.0 * (y * z - x * w)
    m[:, 2, 0] = 2.0 * (x * z - y * w)
    m[:, 2, 1] = 2.0 * (y * z + x * w)
    m[:, 2, 2] = 1.0 - 2.0 * (x * x + y * y)
    return m


# Per-particle rotation columns the patched sim writes when --output_rot is on
# (compute_R_from_F polar rotation as a (w,x,y,z) quaternion). Their PRESENCE in
# the sim ply is what switches the fuser from the CPU-Kabsch fallback to the
# GPU sim-R hot path.
_SIM_ROT_FIELDS = ("rot_w", "rot_x", "rot_y", "rot_z")


def _read_sim_rot_quats(vertex_data: np.ndarray) -> np.ndarray | None:
    """Extract (N, 4) per-particle polar-rotation quaternions (w,x,y,z) from a
    sim ply's vertex record, or None if the --output_rot columns are absent."""
    names = vertex_data.dtype.names or ()
    if not all(f in names for f in _SIM_ROT_FIELDS):
        return None
    return np.stack(
        [vertex_data[f] for f in _SIM_ROT_FIELDS], axis=1,
    ).astype(np.float64)


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
    sim_R0_mats: np.ndarray | None  # (n_kept, 3, 3) frame-0 per-particle polar R
    #   (cube frame) from the GPU sim's --output_rot. Present iff frame 0 carried
    #   rot_* fields; its presence makes fuse_frame use the GPU sim-R path
    #   instead of the CPU-Kabsch fallback.

    # --- fracture-aware re-binding (Phase 1) state ---------------------------
    # Rest pairwise distances + per-pair validity for the stretch test, and the
    # triu index pair the pairwise diffs use. Precomputed once at build time.
    pair_d0: np.ndarray          # (n_ref, P) rest pairwise dists, P = K(K-1)/2
    pair_valid: np.ndarray       # (n_ref, P) per-pair validity (rest dist big)
    pair_iu: np.ndarray          # (P,) triu row indices
    pair_ju: np.ndarray          # (P,) triu col indices
    # MUTABLE latch state (in-place numpy mutation on a frozen dataclass is
    # fine; we never reassign the attribute). Advanced once per fuse_frame call;
    # the per-frame loop calls frames in order, so this latches monotonically.
    over_count: np.ndarray       # (n_ref,) int: consecutive frames over TAU
    latched: np.ndarray          # (n_ref,) bool: splat has fractured + committed
    snap_col: np.ndarray         # (n_ref,) int: KNN column the splat snapped to
    #   (the single nearest neighbour at the latch frame); -1 until latched.

    # Effective per-frame binding the position blend + R gather both read. For
    # un-latched splats these equal the original knn_idx/knn_weights; for a
    # latched splat the row is a one-hot on its snapped neighbour. Rebuilt in
    # place each frame from the (monotone) latch state.
    eff_idx: np.ndarray          # (n_ref, K) effective neighbour indices
    eff_weights: np.ndarray      # (n_ref, K) effective neighbour weights


class KNNKabschFuser:
    """Fuser Protocol impl using inverse-distance K-NN skinning + Kabsch.

    Construction:
        fuser = KNNKabschFuser(k=8)

    The K parameter controls how many sim particles weight each reference splat's
    displacement (higher K = smoother, more diffusive; K=8 is the production default
    per the spec). All other parameters use the prior script's production defaults:
    Y-up -> Z-up rotation, source-scale output, centered at origin.
    """

    def __init__(
        self,
        k: int = 8,
        *,
        enable_fracture: bool = True,
        tau_stretch: float = TAU_STRETCH,
        fracture_patience: int = FRACTURE_PATIENCE,
        source_y_up: bool = False,
    ) -> None:
        if k < 1:
            raise ValueError(f"k must be >= 1; got {k}")
        if tau_stretch <= 1.0:
            raise ValueError(f"tau_stretch must be > 1.0; got {tau_stretch}")
        if fracture_patience < 1:
            raise ValueError(
                f"fracture_patience must be >= 1; got {fracture_patience}"
            )
        self.k = k
        # Source-frame convention. When True (legacy/PhysGaussian path), the
        # fuser applies an Rx(+pi/2) Y-up -> Z-up rotation to source quats,
        # source normals, sim positions, and conjugates the per-frame R into
        # the Z-up world basis. When False (DEFAULT — matches the library
        # `coord_convention="z-up"` invariant + the mpm sim engine's long-
        # standing `--no_zup` intent), all those rotations are skipped: the
        # fuser is a pure pass-through in basis, and the .gsq output stays
        # in the source's native frame. The old default was True, baked in
        # unconditionally; that tipped every Z-up sequence onto its side at
        # render. See server/gsfluent/core/coord_convert.py for the math.
        self.source_y_up = source_y_up
        # Fracture-aware re-binding (Phase 1). enable_fracture=False reproduces
        # the pre-fracture behaviour byte-for-byte (the frozen frame-0 binding),
        # which is exactly what a coherent / non-fracturing neighbourhood gets
        # even when enabled -- the latch never trips.
        self.enable_fracture = enable_fracture
        self.tau_stretch = tau_stretch
        self.fracture_patience = fracture_patience
        # Number of splats re-bound (latched) on the most recent fuse_frame call
        # and a per-frame history. Diagnostic only; lets a driver report the
        # re-bound count per frame without re-deriving it.
        self.last_frame_rebound = 0
        self.rebound_per_frame: list[int] = []
        # Maps id(Correspondence) -> _KNNCorrespondence side-state. The
        # Protocol's Correspondence is a public frozen dataclass; we keep the
        # K-NN map + reference attrs here to avoid leaking large numpy arrays
        # through the public type.
        self._state: dict[int, _KNNCorrespondence] = {}

    def build_correspondence(
        self,
        reference_ply_path: Path,
        first_frame_particles: ParticleFrame,
        first_frame_rot_quats: np.ndarray | None = None,
    ) -> Correspondence:
        """Build the K-NN reference->particle mapping. One-shot per sequence.

        first_frame_rot_quats (optional): (N, 4) per-particle polar-rotation
        quaternions (w,x,y,z) for frame 0, from the GPU sim's --output_rot.
        When supplied, the frame-0 rotation matrices are stashed as the rest
        reference, and fuse_frame uses the GPU sim-R path (no CPU Kabsch SVD).
        When None, the fuser falls back to CPU weighted-Kabsch (unchanged).
        """
        first_frame_particles = np.asarray(first_frame_particles, dtype=np.float32)
        if first_frame_particles.ndim != 2 or first_frame_particles.shape[1] != 3:
            raise FuseError(
                f"first_frame_particles must be (N, 3); got shape "
                f"{first_frame_particles.shape}"
            )
        if not np.isfinite(first_frame_particles).all():
            raise FuseNonFiniteInputError("first_frame_particles contains NaN/Inf")

        # GPU sim-R rest reference: convert frame-0 per-particle quaternions to
        # matrices once. fuse_frame composes each later frame's R against these.
        sim_R0_mats: np.ndarray | None = None
        if first_frame_rot_quats is not None:
            r0 = np.asarray(first_frame_rot_quats, dtype=np.float64)
            if r0.ndim != 2 or r0.shape[1] != 4:
                raise FuseError(
                    f"first_frame_rot_quats must be (N, 4); got {r0.shape}"
                )
            if r0.shape[0] != first_frame_particles.shape[0]:
                raise FuseError(
                    f"first_frame_rot_quats has {r0.shape[0]} rows; expected "
                    f"{first_frame_particles.shape[0]} (matching particles)"
                )
            if not np.isfinite(r0).all():
                raise FuseNonFiniteInputError(
                    "first_frame_rot_quats contains NaN/Inf"
                )
            sim_R0_mats = _quats_wxyz_to_matrices(r0)

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

        # Optional Y-up -> Z-up basis rotation on rotation quats + normals.
        # See __init__ docstring: skipped by default; only applied when the
        # source ply is genuinely Y-up. Either way `rest_quats_wxyz` carries
        # the quats in whatever frame they end up in (source frame when
        # source_y_up=False, Z-up world frame when True) so fuse_frame's
        # delta composition stays in the same basis as the stored splats.
        rest_quats_wxyz: np.ndarray | None = None
        if all(k in full_attrs.dtype.names for k in ("rot_0", "rot_1", "rot_2", "rot_3")):
            q = np.stack([
                full_attrs["rot_0"], full_attrs["rot_1"],
                full_attrs["rot_2"], full_attrs["rot_3"],
            ], axis=1).astype(np.float32)
            new_q = _rotate_quat(q) if self.source_y_up else q
            full_attrs["rot_0"] = new_q[:, 0]
            full_attrs["rot_1"] = new_q[:, 1]
            full_attrs["rot_2"] = new_q[:, 2]
            full_attrs["rot_3"] = new_q[:, 3]
            rest_quats_wxyz = new_q.astype(np.float64)
        if self.source_y_up and all(
            k in full_attrs.dtype.names for k in ("nx", "ny", "nz")
        ):
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

        # --- fracture-aware re-binding (Phase 1) precompute ------------------
        # Rest pairwise distances over each splat's K bound frame-0 neighbours,
        # once. With effective_k < 2 there are no pairs -> the stretch test is a
        # no-op (a single bone carries no divergence signal), and re-binding to
        # a one-hot is already the binding, so fracture is inert at K=1.
        n_ref = len(ref_v)
        p_rest0 = first_frame_particles[knn_idx]               # (n_ref, K, 3)
        pair_d0, pair_valid = _pairwise_rest_dists(p_rest0)
        pair_iu, pair_ju = np.triu_indices(knn_idx.shape[1], k=1)

        corr = Correspondence(
            reference_ply_path=reference_ply_path,
            indices=tuple(int(i) for i in range(n_ref)),
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
            sim_R0_mats=sim_R0_mats,
            pair_d0=pair_d0,
            pair_valid=pair_valid,
            pair_iu=pair_iu.astype(np.intp),
            pair_ju=pair_ju.astype(np.intp),
            over_count=np.zeros(n_ref, dtype=np.int32),
            latched=np.zeros(n_ref, dtype=bool),
            snap_col=np.full(n_ref, -1, dtype=np.intp),
            # Effective binding starts as the original frozen binding; the latch
            # rewrites individual rows to one-hot snaps as splats fracture.
            eff_idx=knn_idx.copy(),
            eff_weights=knn_weights.copy(),
        )
        return corr

    def fuse_frame(
        self,
        correspondence: Correspondence,
        particle_frame: ParticleFrame,
        rot_quats: np.ndarray | None = None,
    ) -> SplatFrame:
        """K-NN-skin per-frame sim displacement onto every reference splat.

        rot_quats (optional): (N, 4) per-particle polar-rotation quaternions
        (w,x,y,z) for THIS frame, from the GPU sim's --output_rot. When supplied
        (and frame-0 R was given to build_correspondence), each splat's rotation
        comes from its bound particles' GPU-computed polar R — no CPU Kabsch SVD.
        When None, the CPU weighted-Kabsch fallback runs (unchanged behaviour).
        """
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

        # --- FRACTURE-AWARE RE-BINDING (Phase 1) -----------------------------
        # Detect, from positions alone, splats whose bound neighbours have flown
        # apart (straddling a crack), latch them with hysteresis, and re-bind
        # each to a single coherent side (hard 1-NN snap). This rewrites the
        # EFFECTIVE binding (eff_idx/eff_weights) in place; the position blend
        # AND the Kabsch R gather below both read that effective binding, so the
        # whole per-splat update follows the sided binding -- no ghost web.
        self._update_fracture_binding(state, particle_frame)
        idx = state.eff_idx
        wts = state.eff_weights

        # --- POSITION: inverse-distance K-NN displacement blend. -------------
        # Reads the effective (possibly re-bound) binding. With fracture off, or
        # for any un-latched splat, idx/wts are the original frozen frame-0 map,
        # so this is byte-identical to the prior position-only skinning.
        sim_disp = particle_frame - state.sim_xyz_t0_kept              # (n_kept, 3)
        neighbors = sim_disp[idx]                                      # (n_ref, K, 3)
        ref_disp = (wts[..., None] * neighbors).sum(axis=1)
        ref_xyz_displaced = state.ref_xyz_norm + ref_disp              # (n_ref, 3)
        out_xyz_world = self._transform_sim_xyz(
            ref_xyz_displaced, extent=state.extent, center=state.center,
        )

        out = state.full_attrs.copy()
        out["x"] = out_xyz_world[:, 0]
        out["y"] = out_xyz_world[:, 1]
        out["z"] = out_xyz_world[:, 2]

        # --- ROTATION: per-splat local rigid rotation. -----------------------
        # Two sources of the per-splat cube-frame delta rotation `r_cube`:
        #   (1) GPU sim-R (HOT PATH): the sim emitted each particle's exact
        #       polar rotation R = polar(F) per frame (--output_rot). For each
        #       splat we gather its bound particles' R, take the delta vs the
        #       frame-0 R, and blend the K via weighted quaternion nlerp. No SVD
        #       runs in the fuser — the rotation was computed on the GPU.
        #   (2) CPU weighted-Kabsch (FALLBACK): when the sim carried no per-
        #       particle R, recover the local rigid rotation from the K
        #       neighbours' (rest -> current) displacements via batched SVD.
        # Both produce `r_cube` (n_ref, 3, 3); the rest of the path — conjugate
        # cube -> Z-up world and compose onto the rest quat — is IDENTICAL, so
        # sim-R is a drop-in replacement for the Kabsch-derived rotation.
        # K=1 carries no orientation signal in the Kabsch fallback -> identity
        # (frozen quat), the pre-existing behaviour. The sim-R path needs no
        # neighbour orientation signal (R is per-particle), so it works for K>=1.
        # Both rotation paths read the EFFECTIVE binding (idx/wts): a latched
        # splat's R is gathered/solved over its single sided neighbour, so its
        # orientation follows that fragment rather than the cross-crack average.
        r_cube: np.ndarray | None = None
        use_sim_r = state.sim_R0_mats is not None and rot_quats is not None
        if state.rest_quats_wxyz is not None:
            if use_sim_r:
                r_cube = self._sim_r_cube(state, rot_quats, idx, wts)
            elif idx.shape[1] >= 2:
                p_rest = state.sim_xyz_t0_kept[idx]                   # (n_ref,K,3)
                p_cur = particle_frame[idx]                          # (n_ref,K,3)
                r_cube = _weighted_kabsch(p_rest, p_cur, wts)

        if r_cube is not None:
            # Cube-frame rotation -> stored basis. When source_y_up=True the
            # stored basis is the Z-up world frame and we conjugate:
            # R_world = R_zup R R_zupᵀ. When False (default) the stored basis
            # IS the cube/source frame, so the conjugation is identity and we
            # use r_cube directly. Mis-applying the conjugation would compose
            # a basis-change onto every per-frame rotation -- splats would
            # spin into the wrong plane every frame.
            r_stored = _R_ZUP @ r_cube @ _R_ZUP.T if self.source_y_up else r_cube
            q_delta = _matrices_to_quats_wxyz(r_stored)              # (n_ref, 4)
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
        # GPU sim-R: if frame 0 carries the sim's per-particle polar-rotation
        # quaternions (--output_rot), bind them as the rest reference so the
        # whole sequence uses the GPU sim-R hot path. Absent -> CPU Kabsch.
        sim_rot_t0 = _read_sim_rot_quats(first_data)
        if sim_rot_t0 is not None:
            print(
                "[fuse] sim per-particle rotation detected (--output_rot) — "
                "using GPU sim-R rotation path (no CPU Kabsch SVD)"
            )

        corr = self.build_correspondence(
            reference_ply_path, sim_xyz_t0, first_frame_rot_quats=sim_rot_t0,
        )

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
            sim_rot = _read_sim_rot_quats(v) if sim_rot_t0 is not None else None
            try:
                result = self.fuse_frame(corr, sim_xyz, rot_quats=sim_rot)
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
    def _sim_r_cube(
        state: "_KNNCorrespondence",
        rot_quats: np.ndarray,
        eff_idx: np.ndarray,
        eff_weights: np.ndarray,
    ) -> np.ndarray:
        """GPU sim-R path: per-splat cube-frame delta rotation from the sim's
        per-particle polar R, gathered through the (effective) KNN binding.

        For each splat and each of its K bound particles, the cube-frame delta
        rotation is R_delta = R_cur · R_0ᵀ (the material rotation of that
        particle since frame 0). The K deltas are blended into a single per-
        splat rotation by weighted quaternion averaging (sign-aligned nlerp,
        the inverse-distance KNN weights), then returned as a (n_ref, 3, 3)
        matrix so it feeds the same cube->Z-up conjugation the Kabsch path uses.

        eff_idx/eff_weights are the FRACTURE-AWARE binding: for an un-latched
        splat they equal the frozen frame-0 map; for a latched splat the row is
        a one-hot on its sided neighbour, so its R follows that single fragment.

        No SVD: the rotation was already computed on the GPU (compute_R_from_F);
        here we only gather, take a matrix delta, blend quaternions, rebuild.
        """
        rq = np.asarray(rot_quats, dtype=np.float64)
        if rq.ndim != 2 or rq.shape[1] != 4:
            raise FuseError(f"rot_quats must be (N, 4); got {rq.shape}")
        if rq.shape[0] != state.sim_xyz_t0_kept.shape[0]:
            raise FuseError(
                f"rot_quats has {rq.shape[0]} rows; expected "
                f"{state.sim_xyz_t0_kept.shape[0]} (matching particles)"
            )
        if not np.isfinite(rq).all():
            raise FuseNonFiniteInputError("rot_quats contains NaN/Inf")

        assert state.sim_R0_mats is not None  # guarded by caller
        r_cur = _quats_wxyz_to_matrices(rq)                 # (n_kept, 3, 3)
        r0 = state.sim_R0_mats                              # (n_kept, 3, 3)
        # Per-particle cube-frame delta since frame 0: R_cur · R_0ᵀ.
        r_delta = np.matmul(r_cur, np.transpose(r0, (0, 2, 1)))  # (n_kept, 3, 3)
        q_delta = _matrices_to_quats_wxyz(r_delta)          # (n_kept, 4) wxyz

        # Gather each splat's K bound particles' delta quaternions + weights,
        # via the effective (possibly re-bound) binding.
        knn_idx = eff_idx                                   # (n_ref, K)
        knn_w = eff_weights                                 # (n_ref, K)
        q_neigh = q_delta[knn_idx]                          # (n_ref, K, 4)

        # Weighted quaternion blend (nlerp). Sign-align every neighbour to the
        # splat's nearest (column 0) so antipodal quaternions don't cancel; the
        # KNN columns are distance-sorted, so column 0 is the closest particle.
        ref_q = q_neigh[:, :1, :]                           # (n_ref, 1, 4)
        signs = np.sign((q_neigh * ref_q).sum(axis=2, keepdims=True))  # (n_ref,K,1)
        signs[signs == 0.0] = 1.0
        q_aligned = q_neigh * signs                         # (n_ref, K, 4)
        q_blended = (knn_w[..., None] * q_aligned).sum(axis=1)  # (n_ref, 4)
        norms = np.linalg.norm(q_blended, axis=1, keepdims=True)
        # A blend that cancels to ~0 (pathological) falls back to the nearest.
        bad = norms[:, 0] < 1e-8
        if bad.any():
            q_blended[bad] = q_neigh[bad, 0, :]
            norms = np.linalg.norm(q_blended, axis=1, keepdims=True)
        q_blended = q_blended / np.maximum(norms, 1e-300)
        return _quats_wxyz_to_matrices(q_blended)           # (n_ref, 3, 3)

    def _update_fracture_binding(
        self,
        state: "_KNNCorrespondence",
        particle_frame: np.ndarray,
    ) -> None:
        """Per-frame fracture detection + monotone-latched re-binding (Phase 1).

        Mutates state's latch arrays + effective binding IN PLACE. The driver
        calls fuse_frame in strict frame order, so the latch advances monotone:

          1. DETECT: for each NOT-yet-latched splat, compute the max relative
             pairwise stretch dt/d0 over its bound neighbours. stretch > tau
             means two neighbours have flown apart -> the splat straddles a
             crack (proposal §2.1). False positives from fast-but-intact bulk
             motion are rejected by construction: a rigid translation leaves
             every pairwise distance unchanged, so stretch stays ~1.
          2. HYSTERESIS: increment a per-splat over-threshold counter; reset it
             on any frame at/under tau. A splat LATCHES only once the counter
             reaches fracture_patience consecutive frames, rejecting a single
             noisy FLIP frame and preventing flip-flop (§2.4).
          3. RE-BIND (R1 hard 1-NN snap): on the latch frame, snap the splat to
             its CURRENT single nearest bound neighbour (one-hot weights). Cracks
             don't heal in these recipes, so the decision is kept for the rest of
             the sequence -> the binding is piecewise-constant in time.

        Records self.last_frame_rebound (newly-latched this frame) and appends
        the cumulative latched count to self.rebound_per_frame.

        No-op (and byte-identical to the pre-fracture behaviour) when
        enable_fracture is False or there are fewer than 2 neighbours (K<2 ->
        no pairs -> no divergence signal; a one-hot snap is already the binding).
        """
        k = state.knn_idx.shape[1]
        if not self.enable_fracture or k < 2:
            self.last_frame_rebound = 0
            self.rebound_per_frame.append(int(state.latched.sum()))
            return

        # Only test splats that haven't latched yet (monotone decision).
        active = ~state.latched
        newly = 0
        if active.any():
            act = np.flatnonzero(active)
            p_cur = particle_frame[state.knn_idx[act]]           # (m, K, 3)
            stretch = _max_pairwise_stretch(
                p_cur,
                state.pair_d0[act],
                state.pair_valid[act],
                state.pair_iu,
                state.pair_ju,
            )                                                    # (m,)

            over = stretch > self.tau_stretch
            # Hysteresis counter: bump where over, reset where not.
            state.over_count[act[over]] += 1
            state.over_count[act[~over]] = 0

            # Latch any splat whose counter has reached patience.
            latch_now = act[state.over_count[act] >= self.fracture_patience]
            if latch_now.size:
                state.latched[latch_now] = True
                # R1 hard 1-NN snap: bind each newly-fractured splat to its
                # CURRENT single nearest bound neighbour. Snap to the side the
                # splat is closest to NOW so it follows a coherent fragment.
                cur_neigh = particle_frame[state.knn_idx[latch_now]]  # (l, K, 3)
                # The splat's own advected rest position would be ideal, but the
                # cheapest robust proxy is the current nearest bound particle.
                # Distance of each bound neighbour to the splat's frame-0 rest
                # neighbour-centroid is ambiguous post-fracture; instead pick the
                # neighbour nearest the splat's current blended position, which
                # is the per-splat weighted centroid of its bound neighbours.
                centroid = (
                    state.knn_weights[latch_now][..., None] * cur_neigh
                ).sum(axis=1, keepdims=True)                     # (l, 1, 3)
                d = np.linalg.norm(cur_neigh - centroid, axis=2)  # (l, K)
                snap = np.argmin(d, axis=1).astype(np.intp)       # (l,)
                state.snap_col[latch_now] = snap

                # Rewrite the effective binding rows to a one-hot on the snapped
                # neighbour (idx repeated across K, weight 1 on col 0, 0 else).
                snapped_part = state.knn_idx[latch_now, snap]     # (l,)
                state.eff_idx[latch_now] = snapped_part[:, None]  # broadcast (l,K)
                state.eff_weights[latch_now] = 0.0
                state.eff_weights[latch_now, 0] = 1.0
                newly = int(latch_now.size)

        self.last_frame_rebound = newly
        self.rebound_per_frame.append(int(state.latched.sum()))

    def _transform_sim_xyz(
        self,
        sim_xyz: np.ndarray,
        *,
        extent: float,
        center: np.ndarray,
    ) -> np.ndarray:
        """Un-normalize back to source-world scale, center at origin, then
        optionally apply the Y-up -> Z-up basis rotation (gated on
        `self.source_y_up`; see __init__)."""
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
        return _rotate_pos(stacked) if self.source_y_up else stacked
