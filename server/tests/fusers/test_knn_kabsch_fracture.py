"""Fracture-aware re-binding in KNNKabschFuser (Phase 1).

The frozen frame-0 K-NN binding makes a splat that straddles a crack average
two diverging motions -> it stretches across the gap (the demolition "ghost
web"). Phase 1 detects fracture from POSITIONS ALONE (a splat's bound
neighbours flying apart, dt/d0 > tau), latches with hysteresis, and re-binds
the splat to one coherent side (hard 1-NN snap). The re-binding composes with
the existing position + rotation update.

All pure / CPU; no GPU, no sim, no codec. Layers:
  1. the detection helpers in isolation;
  2. end-to-end: a straddling splat follows ONE diverging side, not the average;
  3. a coherent neighbourhood is byte-identical to fracture-off (today);
  4. hysteresis rejects a single-frame spike (no flip-flop) and the latch is
     monotone;
  5. finite / no-NaN under fracture; diagnostics (rebound counts).
"""
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.fusers.knn_kabsch import (
    FRACTURE_PATIENCE,
    TAU_STRETCH,
    KNNKabschFuser,
    _max_pairwise_stretch,
    _pairwise_rest_dists,
)


# --- helpers ----------------------------------------------------------------


def _write_full_3dgs_ply(path: Path, pts: np.ndarray) -> None:
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
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


# --- 1. detection helpers in isolation --------------------------------------


def test_pairwise_rest_dists_shapes_and_values() -> None:
    """d0 covers all K(K-1)/2 unique pairs; a near-coincident pair is masked."""
    # 3 neighbours: pts at distance 1 apart, plus one coincident with another.
    p_rest = np.array([[[0.0, 0, 0], [1.0, 0, 0], [0.0, 0, 0]]])  # (1, 3, 3)
    d0, valid = _pairwise_rest_dists(p_rest)
    # P = 3 pairs: (0,1)=1, (0,2)=0, (1,2)=1
    assert d0.shape == (1, 3)
    np.testing.assert_allclose(d0[0], [1.0, 0.0, 1.0])
    # the coincident (0,2) pair is invalid (tiny rest distance)
    assert valid[0, 1] == False  # noqa: E712
    assert valid[0, 0] == True and valid[0, 2] == True  # noqa: E712


def test_max_pairwise_stretch_coherent_is_one() -> None:
    """A rigidly translated neighbourhood: every pairwise distance unchanged ->
    stretch == 1 (this is why fast-but-intact bulk motion never trips tau)."""
    rng = np.random.default_rng(0)
    p_rest = rng.uniform(-1, 1, (4, 8, 3))
    iu, ju = np.triu_indices(8, k=1)
    d0, valid = _pairwise_rest_dists(p_rest)
    p_cur = p_rest + np.array([100.0, -50.0, 30.0])  # huge rigid translation
    s = _max_pairwise_stretch(p_cur, d0, valid, iu, ju)
    np.testing.assert_allclose(s, 1.0, atol=1e-9)


def test_max_pairwise_stretch_detects_split() -> None:
    """Pull two halves of a neighbourhood apart -> stretch >> 1."""
    p_rest = np.array([[[0.0, 0, 0], [0.1, 0, 0], [0.2, 0, 0], [0.3, 0, 0]]])
    iu, ju = np.triu_indices(4, k=1)
    d0, valid = _pairwise_rest_dists(p_rest)
    p_cur = p_rest.copy()
    p_cur[0, 2:] += np.array([5.0, 0, 0])  # far half flies away
    s = _max_pairwise_stretch(p_cur, d0, valid, iu, ju)
    assert s[0] > 5.0


# --- 2. end-to-end: straddling splat follows one side -----------------------


def _two_group_sim(gap_split: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a sim cloud of two tight groups around x=0, then a frame where the
    right group translates +x by `gap_split` (a crack opening). The reference
    splat sits exactly between the groups (straddles the crack).

    Returns (sim0, sim_t, ref_pt) all in the normalized-cube band [~1].
    """
    rng = np.random.default_rng(7)
    # two compact groups, left around x=0.9, right around x=1.1, in the cube band
    left = np.column_stack([
        rng.uniform(0.85, 0.95, 30),
        rng.uniform(0.95, 1.05, 30),
        rng.uniform(0.95, 1.05, 30),
    ])
    right = np.column_stack([
        rng.uniform(1.05, 1.15, 30),
        rng.uniform(0.95, 1.05, 30),
        rng.uniform(0.95, 1.05, 30),
    ])
    sim0 = np.vstack([left, right]).astype(np.float32)
    sim_t = sim0.copy()
    sim_t[30:, 0] += gap_split  # right group translates away -> crack opens
    # A small spread of straddling reference splats around the crack centre.
    # (>=2 points with spread so the cube-normalization has non-zero extent.)
    # The first point sits exactly on the crack centre (the index-0 splat the
    # straddle assertions key on); the rest hug it tightly.
    ref_pt = np.array(
        [[1.00, 1.00, 1.00],
         [0.99, 1.00, 1.00],
         [1.01, 1.00, 1.00],
         [1.00, 0.99, 1.00],
         [1.00, 1.00, 1.01]],
        dtype=np.float32,
    )
    return sim0, sim_t, ref_pt


def test_straddling_splat_rebinds_to_one_side(tmp_path: Path) -> None:
    """A splat whose neighbours split into two diverging groups re-binds to ONE
    side: its trajectory follows that side, not the cross-crack average."""
    sim0, sim_t, ref_pt = _two_group_sim(gap_split=2.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)

    # fracture ON: patience=1 so a single divergent frame latches (deterministic)
    f = KNNKabschFuser(k=8, fracture_patience=1, tau_stretch=TAU_STRETCH)
    corr = f.build_correspondence(ref, sim0)
    out_frac = f.fuse_frame(corr, sim_t)["xyz"][0]

    # fracture OFF: the legacy cross-crack average (the ghost web)
    f_off = KNNKabschFuser(k=8, enable_fracture=False)
    corr_off = f_off.build_correspondence(ref, sim0)
    out_avg = f_off.fuse_frame(corr_off, sim_t)["xyz"][0]

    # the straddling splat (index 0) latched
    state = f._state[id(corr)]
    assert state.latched[0]
    assert f.last_frame_rebound >= 1

    # The re-bound splat must follow ONE coherent side EXACTLY (a single bound
    # particle's motion), not the cross-crack average. Reconstruct what each
    # outcome should be in the normalized cube frame:
    #   - re-bound: rest + the single snapped particle's displacement
    #   - average (ghost): rest + inverse-distance blend over BOTH sides
    snap_part = state.knn_idx[0, state.snap_col[0]]
    disp_one = sim_t[snap_part] - sim0[snap_part]
    expect_frac = f._transform_sim_xyz(
        (state.ref_xyz_norm[0] + disp_one)[None],
        extent=state.extent, center=state.center,
    )[0]
    np.testing.assert_allclose(out_frac, expect_frac, atol=1e-5)

    # The snapped particle belongs to ONE group; reconstruct the pure-left and
    # pure-right outcomes (a splat following only the stationary vs only the
    # moving side). The re-bound result must equal one of them EXACTLY, while
    # the legacy average lands strictly BETWEEN -- i.e. it is the cross-crack
    # blend (the ghost web), the thing Phase 1 eliminates.
    left_part = state.knn_idx[0][np.isin(state.knn_idx[0], np.arange(30))][0]
    right_part = state.knn_idx[0][np.isin(state.knn_idx[0], np.arange(30, 60))][0]
    x_left = f._transform_sim_xyz(
        (state.ref_xyz_norm[0] + (sim_t[left_part] - sim0[left_part]))[None],
        extent=state.extent, center=state.center,
    )[0, 0]
    x_right = f._transform_sim_xyz(
        (state.ref_xyz_norm[0] + (sim_t[right_part] - sim0[right_part]))[None],
        extent=state.extent, center=state.center,
    )[0, 0]
    lo, hi = sorted([x_left, x_right])
    # the average is strictly inside the (left, right) interval -> a blend
    assert lo < out_avg[0] < hi
    # the re-bound result is at one endpoint (not inside) -> committed to a side
    assert np.isclose(out_frac[0], lo, atol=1e-5) or np.isclose(out_frac[0], hi, atol=1e-5)


def test_rebound_splat_follows_snapped_particle(tmp_path: Path) -> None:
    """After re-bind (1-NN snap) the splat rigidly tracks its single snapped
    sim particle's displacement -- exactly, not a blend."""
    sim0, sim_t, ref_pt = _two_group_sim(gap_split=3.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)

    f = KNNKabschFuser(k=8, fracture_patience=1)
    corr = f.build_correspondence(ref, sim0)
    state = f._state[id(corr)]

    f.fuse_frame(corr, sim_t)  # triggers latch
    assert state.latched[0]
    snap_col = state.snap_col[0]
    snapped_particle = state.knn_idx[0, snap_col]

    # the effective binding is a one-hot on the snapped particle
    assert np.all(state.eff_idx[0] == snapped_particle)
    np.testing.assert_allclose(state.eff_weights[0, 0], 1.0)
    np.testing.assert_allclose(state.eff_weights[0, 1:], 0.0)

    # the normalized output position = rest + that single particle's displacement
    expect_norm = state.ref_xyz_norm[0] + (sim_t[snapped_particle] - sim0[snapped_particle])
    expect_world = f._transform_sim_xyz(
        expect_norm[None], extent=state.extent, center=state.center
    )[0]
    got = f.fuse_frame(corr, sim_t)["xyz"][0]
    np.testing.assert_allclose(got, expect_world, atol=1e-5)


# --- 3. coherent neighbourhood unaffected (identical to today) --------------


def test_coherent_neighbourhood_identical_to_fracture_off(tmp_path: Path) -> None:
    """A non-fracturing (coherent) deformation must be byte-identical with
    fracture ON vs OFF -- the latch never trips, so nothing re-binds."""
    rng = np.random.default_rng(3)
    ref_pts = rng.uniform(0.6, 1.4, (40, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    sim0 = rng.uniform(0.5, 1.5, (120, 3)).astype(np.float32)
    # coherent motion: a gentle uniform scale + translation (no neighbour split)
    sim_t = (sim0 * 1.05 + np.array([0.2, -0.1, 0.05])).astype(np.float32)

    f_on = KNNKabschFuser(k=8)  # fracture enabled, defaults
    f_off = KNNKabschFuser(k=8, enable_fracture=False)
    c_on = f_on.build_correspondence(ref, sim0)
    c_off = f_off.build_correspondence(ref, sim0)

    out_on = f_on.fuse_frame(c_on, sim_t)
    out_off = f_off.fuse_frame(c_off, sim_t)

    assert f_on.last_frame_rebound == 0
    assert not f_on._state[id(c_on)].latched.any()
    np.testing.assert_array_equal(out_on["xyz"], out_off["xyz"])
    np.testing.assert_array_equal(
        out_on["full_attrs"]["rot_0"], out_off["full_attrs"]["rot_0"]
    )


def test_coherent_sequence_never_rebinds(tmp_path: Path) -> None:
    """A multi-frame coherent deformation never latches any splat."""
    rng = np.random.default_rng(5)
    ref_pts = rng.uniform(0.6, 1.4, (30, 3)).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)
    sim0 = rng.uniform(0.5, 1.5, (90, 3)).astype(np.float32)

    f = KNNKabschFuser(k=8)
    corr = f.build_correspondence(ref, sim0)
    for s in np.linspace(1.0, 1.15, 10):  # gentle progressive scale
        sim_t = (sim0 * s).astype(np.float32)
        f.fuse_frame(corr, sim_t)
    assert sum(f.rebound_per_frame) == 0
    assert not f._state[id(corr)].latched.any()


# --- 4. hysteresis: no flip-flop, monotone latch ----------------------------


def test_hysteresis_single_spike_does_not_latch(tmp_path: Path) -> None:
    """A single noisy over-threshold frame (then back to coherent) must NOT
    latch with default patience >= 2 -- the splat does not flip-flop."""
    assert FRACTURE_PATIENCE >= 2  # the default that makes this meaningful
    sim0, sim_spike, ref_pt = _two_group_sim(gap_split=3.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)

    f = KNNKabschFuser(k=8)  # default patience (>=2)
    corr = f.build_correspondence(ref, sim0)

    # frame 1: a one-off divergent spike
    f.fuse_frame(corr, sim_spike)
    assert f.last_frame_rebound == 0  # not enough consecutive frames
    assert not f._state[id(corr)].latched.any()
    # frame 2: back to coherent (groups together) -> counter resets, no latch
    f.fuse_frame(corr, sim0)
    assert not f._state[id(corr)].latched.any()
    # frame 3: coherent again -> still nothing
    f.fuse_frame(corr, sim0)
    assert sum(f.last_frame_rebound for _ in [0]) == 0
    assert not f._state[id(corr)].latched.any()


def test_hysteresis_sustained_divergence_latches_after_patience(tmp_path: Path) -> None:
    """Sustained over-threshold stretch latches only after `patience`
    consecutive frames, then stays latched (monotone)."""
    sim0, sim_split, ref_pt = _two_group_sim(gap_split=3.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)

    patience = 3
    f = KNNKabschFuser(k=8, fracture_patience=patience)
    corr = f.build_correspondence(ref, sim0)
    state = f._state[id(corr)]

    for frame in range(patience + 2):
        f.fuse_frame(corr, sim_split)
        if frame < patience - 1:
            assert not state.latched.any(), f"latched too early at frame {frame}"
        else:
            assert state.latched[0], f"should be latched by frame {frame}"

    # monotone: once latched, the snap column never changes even if we now feed
    # a frame where the OTHER side would be nearer
    snap_before = state.snap_col[0]
    sim_other = sim0.copy()
    sim_other[:30, 0] -= 5.0  # now the left group flies away instead
    f.fuse_frame(corr, sim_other)
    assert state.snap_col[0] == snap_before
    assert state.latched[0]


def test_latch_is_monotone_across_sequence(tmp_path: Path) -> None:
    """The cumulative latched count is non-decreasing over a fracturing
    sequence (cracks don't heal)."""
    sim0, sim_split, ref_pt = _two_group_sim(gap_split=4.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)

    f = KNNKabschFuser(k=8, fracture_patience=1)
    corr = f.build_correspondence(ref, sim0)
    # progressively widen, then partially close: latched count must not drop
    for g in [0.0, 1.0, 2.0, 3.0, 1.5, 0.5, 0.0]:
        st = sim0.copy()
        st[30:, 0] += g
        f.fuse_frame(corr, st)
    counts = f.rebound_per_frame
    assert all(counts[i] <= counts[i + 1] for i in range(len(counts) - 1))
    assert counts[-1] >= 1  # something fractured and stayed fractured


# --- 5. finite / no-NaN + diagnostics ---------------------------------------


def test_fracture_output_is_finite(tmp_path: Path) -> None:
    """Re-binding never produces NaN/Inf positions or quaternions."""
    sim0, sim_t, ref_pt = _two_group_sim(gap_split=10.0)  # extreme split
    # add more straddling splats
    rng = np.random.default_rng(2)
    extra = rng.uniform(0.9, 1.1, (20, 3)).astype(np.float32)
    ref_pts = np.vstack([ref_pt, extra]).astype(np.float32)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pts)

    f = KNNKabschFuser(k=8, fracture_patience=1)
    corr = f.build_correspondence(ref, sim0)
    out = f.fuse_frame(corr, sim_t)
    assert np.isfinite(out["xyz"]).all()
    for c in ("rot_0", "rot_1", "rot_2", "rot_3"):
        assert np.isfinite(out["full_attrs"][c]).all()
    # quaternions stay unit-norm
    q = np.stack([out["full_attrs"][f"rot_{i}"] for i in range(4)], axis=1)
    np.testing.assert_allclose(np.linalg.norm(q, axis=1), 1.0, atol=1e-5)


def test_rebound_per_frame_tracks_latches(tmp_path: Path) -> None:
    """The diagnostic rebound_per_frame is the cumulative latched count, one
    entry per fuse_frame call."""
    sim0, _, ref_pt = _two_group_sim(gap_split=3.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)
    f = KNNKabschFuser(k=8, fracture_patience=1)
    corr = f.build_correspondence(ref, sim0)
    for g in [0.0, 3.0, 3.0]:
        st = sim0.copy()
        st[30:, 0] += g
        f.fuse_frame(corr, st)
    assert len(f.rebound_per_frame) == 3
    assert f.rebound_per_frame[0] == 0  # frame 0: no split
    assert f.rebound_per_frame[-1] >= 1


def test_fracture_disabled_leaves_binding_frozen(tmp_path: Path) -> None:
    """enable_fracture=False: the effective binding equals the frame-0 binding
    forever, even under an extreme split."""
    sim0, sim_t, ref_pt = _two_group_sim(gap_split=20.0)
    ref = tmp_path / "ref.ply"
    _write_full_3dgs_ply(ref, ref_pt)
    f = KNNKabschFuser(k=8, enable_fracture=False)
    corr = f.build_correspondence(ref, sim0)
    state = f._state[id(corr)]
    f.fuse_frame(corr, sim_t)
    np.testing.assert_array_equal(state.eff_idx, state.knn_idx)
    np.testing.assert_array_equal(state.eff_weights, state.knn_weights)
    assert not state.latched.any()


def test_invalid_tau_and_patience_rejected() -> None:
    """Constructor guards: tau must be > 1, patience >= 1."""
    with pytest.raises(ValueError):
        KNNKabschFuser(tau_stretch=1.0)
    with pytest.raises(ValueError):
        KNNKabschFuser(fracture_patience=0)
