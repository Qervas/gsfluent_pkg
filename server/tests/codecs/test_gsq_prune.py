"""Tests for .gsq significance pruning."""
import numpy as np
import pytest

from gsfluent.core.codecs.gsq_prune import (
    compute_significance,
    select_keep_indices,
    retention_curve,
)


def test_significance_is_opacity_times_volume() -> None:
    # opacity normalized in [0,1], scales positive
    opacity = np.array([1.0, 0.5, 0.01], dtype=np.float32)
    scales = np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    sig = compute_significance(opacity, scales)
    # equal scales → significance proportional to opacity
    assert sig[0] > sig[1] > sig[2]
    assert np.isclose(sig[0] / sig[1], 2.0, rtol=1e-5)


def test_significance_rewards_bigger_splats() -> None:
    opacity = np.array([1.0, 1.0], dtype=np.float32)
    scales = np.array([[2.0, 2.0, 2.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    sig = compute_significance(opacity, scales)
    # 2× scale per axis → 8× volume
    assert np.isclose(sig[0] / sig[1], 8.0, rtol=1e-5)


def test_select_keep_indices_keeps_top_k_by_significance() -> None:
    sig = np.array([0.1, 0.9, 0.5, 0.01, 0.7], dtype=np.float32)
    keep = select_keep_indices(sig, keep_count=3)
    # top 3 by significance are indices 1 (0.9), 4 (0.7), 2 (0.5)
    assert set(keep.tolist()) == {1, 2, 4}
    # keep is sorted ascending (so downstream slicing preserves original order)
    assert list(keep) == sorted(keep)


def test_retention_curve_reports_count_per_retention() -> None:
    # 4 splats; significance 0.97, 0.02, 0.005, 0.005 → total 1.0
    sig = np.array([0.97, 0.02, 0.005, 0.005], dtype=np.float32)
    curve = retention_curve(sig, retentions=(0.99, 0.97, 0.95))
    # to retain 0.97 of contribution, the single top splat (0.97) suffices → keep 1
    r97 = next(c for c in curve if c["retention"] == 0.97)
    assert r97["keep_count"] == 1
    assert np.isclose(r97["prune_ratio"], 0.75, rtol=1e-6)
    # to retain 0.99, need top splat + next (0.97+0.02=0.99) → keep 2
    r99 = next(c for c in curve if c["retention"] == 0.99)
    assert r99["keep_count"] == 2


def test_keep_count_clamped_to_n() -> None:
    sig = np.array([0.5, 0.5], dtype=np.float32)
    keep = select_keep_indices(sig, keep_count=10)
    assert len(keep) == 2


import io
import struct
import zstandard as zstd


def _make_tiny_gsq(n_splats: int, n_frames: int) -> bytes:
    """Build a minimal valid GSQ1 file in memory for tests. Mirrors
    server/gsfluent/core/codecs/gsq.py layout exactly."""
    MAGIC = b"GSQ1"; VERSION = 1
    HEADER_SIZE = 80; INDEX_ENTRY = 16
    cctx = zstd.ZstdCompressor(level=1)
    rng = np.random.default_rng(0)
    rgb = rng.random((n_splats, 3)).astype(np.float16)
    opacity = (rng.random(n_splats) * 255).astype(np.uint8)
    scales = (rng.random((n_splats, 3)).astype(np.float16))
    static = rgb.tobytes() + opacity.tobytes() + scales.tobytes()
    static_c = cctx.compress(static)
    static_off = HEADER_SIZE + n_frames * INDEX_ENTRY
    frames_c = []
    for _ in range(n_frames):
        xyz = rng.integers(-100, 100, (n_splats, 3), dtype=np.int16)
        qxyz = rng.integers(-100, 100, (n_splats, 3), dtype=np.int16)
        frames_c.append(cctx.compress(xyz.tobytes() + qxyz.tobytes()))
    out = io.BytesIO()
    out.write(MAGIC)
    out.write(struct.pack("<III", VERSION, n_splats, n_frames))
    out.write(struct.pack("<f", 24.0))
    out.write(np.array([-1, -1, -1], dtype=np.float32).tobytes())
    out.write(np.array([1, 1, 1], dtype=np.float32).tobytes())
    out.write(struct.pack("<QI", static_off, len(static_c)))
    out.write(b"\x00" * 24)
    off = static_off + len(static_c)
    for c in frames_c:
        out.write(struct.pack("<QII", off, len(c), 0)); off += len(c)
    out.write(static_c)
    for c in frames_c:
        out.write(c)
    return out.getvalue()


def test_prune_reduces_n_splats_and_stays_valid() -> None:
    from gsfluent.core.codecs.gsq_prune import prune_gsq_bytes
    from gsfluent.core.codecs.gsq import parse_header_bytes  # see Task 3 note

    raw = _make_tiny_gsq(n_splats=100, n_frames=5)
    keep = np.sort(np.random.default_rng(1).choice(100, 40, replace=False))
    pruned = prune_gsq_bytes(raw, keep)

    # The pruned file parses, has n_splats == len(keep), same n_frames.
    h = parse_header_bytes(pruned)
    assert h["n_splats"] == 40
    assert h["n_frames"] == 5
    assert pruned[:4] == b"GSQ1"


def test_prune_preserves_kept_frame_data_losslessly() -> None:
    """Raw int16 slicing must keep the exact bytes of kept splats."""
    from gsfluent.core.codecs.gsq_prune import prune_gsq_bytes
    from gsfluent.core.codecs.gsq import decode_frame_raw_i16  # see Task 3 note

    raw = _make_tiny_gsq(n_splats=50, n_frames=3)
    keep = np.array([0, 7, 49], dtype=np.int64)
    pruned = prune_gsq_bytes(raw, keep)

    for fidx in range(3):
        orig_xyz, orig_q = decode_frame_raw_i16(raw, fidx)
        new_xyz, new_q = decode_frame_raw_i16(pruned, fidx)
        np.testing.assert_array_equal(new_xyz, orig_xyz[keep])
        np.testing.assert_array_equal(new_q, orig_q[keep])


def test_prune_is_smaller() -> None:
    from gsfluent.core.codecs.gsq_prune import prune_gsq_bytes
    raw = _make_tiny_gsq(n_splats=200, n_frames=4)
    keep = np.arange(0, 200, 4)  # keep 25%
    pruned = prune_gsq_bytes(raw, keep)
    assert len(pruned) < len(raw)


# ---- prune_to_retention: the single helper shared by CLI + pack pipeline ----


def test_prune_to_retention_reduces_splats_and_stays_valid() -> None:
    """Pruning at a sub-1.0 retention must drop splat count and keep the
    file a valid, parseable GSQ1."""
    from gsfluent.core.codecs.gsq_prune import prune_to_retention
    from gsfluent.core.codecs.gsq import parse_header_bytes

    raw = _make_tiny_gsq(n_splats=500, n_frames=4)
    n_before = parse_header_bytes(raw)["n_splats"]
    pruned = prune_to_retention(raw, 0.98)
    h = parse_header_bytes(pruned)
    # random opacity×volume → 0.98 retention drops a meaningful fraction
    assert h["n_splats"] < n_before
    assert h["n_frames"] == 4
    assert pruned[:4] == b"GSQ1"
    assert len(pruned) < len(raw)


def test_prune_to_retention_matches_manual_pipeline() -> None:
    """The helper must produce exactly what compute→curve→select→prune does
    by hand, so the CLI and pack pipeline can't drift from each other."""
    from gsfluent.core.codecs.gsq_prune import (
        prune_to_retention, prune_gsq_bytes, select_keep_indices,
        retention_curve, compute_significance,
    )
    from gsfluent.core.codecs.gsq import parse_header_bytes
    import zstandard as zstd

    raw = _make_tiny_gsq(n_splats=300, n_frames=3)
    h = parse_header_bytes(raw)
    n = h["n_splats"]
    static = zstd.ZstdDecompressor().decompress(
        bytes(raw[h["static_offset"]:h["static_offset"] + h["static_size"]])
    )
    op = np.frombuffer(static[n * 3 * 2:n * 3 * 2 + n], dtype=np.uint8).astype(np.float32) / 255.0
    sc = np.frombuffer(static[n * 3 * 2 + n:n * 3 * 2 + n + n * 3 * 2], dtype=np.float16).reshape(n, 3).astype(np.float32)
    sig = compute_significance(op, sc)
    kc = retention_curve(sig, (0.99,))[0]["keep_count"]
    expected = prune_gsq_bytes(raw, select_keep_indices(sig, kc))

    assert prune_to_retention(raw, 0.99) == expected


def test_prune_to_retention_full_retention_is_noop() -> None:
    """retention == 1.0 keeps every splat → returns the original bytes."""
    from gsfluent.core.codecs.gsq_prune import prune_to_retention
    raw = _make_tiny_gsq(n_splats=100, n_frames=2)
    assert prune_to_retention(raw, 1.0) is raw


def test_prune_to_retention_rejects_bad_retention() -> None:
    from gsfluent.core.codecs.gsq_prune import prune_to_retention
    raw = _make_tiny_gsq(n_splats=10, n_frames=1)
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError):
            prune_to_retention(raw, bad)
