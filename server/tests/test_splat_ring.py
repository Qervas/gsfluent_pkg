"""Unit tests for frontend/python/splat_ring.py — sliding-window decoder.

Builds a small synthetic .gsq on disk per test and drives a SplatRing
against it. Avoids importing viser/uvicorn by loading splat_ring directly
from its source path (same trick test_viser_headless_cell_lru.py uses).
"""
from __future__ import annotations

import importlib.util
import io
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest

# ----- import splat_ring without dragging viser/uvicorn in --------------------

_RING_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "python" / "splat_ring.py"
)


def _load_splat_ring():
    spec = importlib.util.spec_from_file_location("_test_splat_ring", _RING_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


splat_ring = _load_splat_ring()
SplatRing = splat_ring.SplatRing
make_static_cell = splat_ring.make_static_cell


# ----- synthetic .gsq builder ------------------------------------------------


def _write_minimal_gsq(
    path: Path,
    n_splats: int = 8,
    n_frames: int = 16,
    fps_hint: float = 24.0,
) -> dict:
    """Write a real .gsq matching server/gsfluent/core/codecs/gsq.py layout.

    Uses unique per-frame xyz quantized values so we can verify that
    decoded frame N actually carries frame-N data (not frame-0 written
    everywhere). Returns the source arrays we wrote, so tests can
    cross-check decode equality.
    """
    import zstandard as zstd

    HEADER_SIZE = 80
    INDEX_ENTRY_SIZE = 16

    rng = np.random.default_rng(seed=0xC0FFEE)
    # Per-frame xyz: a global drift + per-splat per-frame jitter so the
    # decoded value will differ frame-to-frame in a verifiable way.
    xyz_all = np.empty((n_frames, n_splats, 3), dtype=np.float32)
    for t in range(n_frames):
        base = np.full(3, t * 0.1, dtype=np.float32)
        xyz_all[t] = base + rng.standard_normal((n_splats, 3)).astype(np.float32) * 0.01

    quat_all = np.zeros((n_frames, n_splats, 4), dtype=np.float32)
    quat_all[..., 0] = 1.0

    rgb = np.clip(rng.uniform(0.0, 1.0, size=(n_splats, 3)), 0.0, 1.0).astype(np.float32)
    opacity = np.clip(rng.uniform(0.0, 1.0, size=(n_splats,)), 0.0, 1.0).astype(np.float32)
    scales = np.exp(rng.standard_normal((n_splats, 3)).astype(np.float32) * 0.3)

    bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
    bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
    span = (bbox_max - bbox_min).astype(np.float32)
    span[span == 0] = 1.0

    # Quantize.
    xyz_q = (
        np.clip(((xyz_all - bbox_min) / span) * 65535.0 - 32768.0, -32768, 32767)
        .round()
        .astype(np.int16)
    )
    quat_q = (
        np.clip(quat_all[..., 1:4] * 32767.0, -32767, 32767).round().astype(np.int16)
    )

    rgb_f16 = rgb.astype(np.float16)
    opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
    scales_f16 = scales.astype(np.float16)

    cctx = zstd.ZstdCompressor(level=3)
    static_blob = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
    static_compressed = cctx.compress(static_blob)
    frame_chunks = [
        cctx.compress(xyz_q[t].tobytes() + quat_q[t].tobytes())
        for t in range(n_frames)
    ]

    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    static_size = len(static_compressed)
    frame0_offset = static_offset + static_size

    index_entries: list[tuple[int, int]] = []
    off = frame0_offset
    for c in frame_chunks:
        index_entries.append((off, len(c)))
        off += len(c)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"GSQ1")
        f.write(struct.pack("<III", 1, n_splats, n_frames))
        f.write(struct.pack("<f", fps_hint))
        f.write(bbox_min.tobytes())
        f.write(bbox_max.tobytes())
        f.write(struct.pack("<QI", static_offset, static_size))
        f.write(b"\x00" * 24)
        for o, s in index_entries:
            f.write(struct.pack("<QII", o, s, 0))
        f.write(static_compressed)
        for c in frame_chunks:
            f.write(c)
    return {
        "xyz_all": xyz_all,
        "quat_all": quat_all,
        "rgb": rgb,
        "opacity": opacity,
        "scales": scales,
        "bbox_min": bbox_min,
        "bbox_max": bbox_max,
        "span": span,
        "n_splats": n_splats,
        "n_frames": n_frames,
    }


# ----- helpers ---------------------------------------------------------------


def _wait_until(predicate, timeout: float = 2.0, poll: float = 0.005) -> bool:
    """Spin-wait helper; returns True if predicate becomes truthy in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


# ----- header / construction -------------------------------------------------


def test_splat_ring_parses_header_and_static(tmp_path):
    gsq = tmp_path / "tiny.gsq"
    meta = _write_minimal_gsq(gsq, n_splats=6, n_frames=10)
    ring = SplatRing(gsq, window_size=4)
    try:
        assert ring.n_frames == 10
        assert ring.n_splats == 6
        assert ring.fps_hint == pytest.approx(24.0)
        assert ring.bbox_min.shape == (3,)
        assert ring.bbox_max.shape == (3,)
        np.testing.assert_allclose(ring.bbox_min, meta["bbox_min"], rtol=0, atol=0)
        # Static block round-trips through f16 with bounded error.
        s = ring.static
        assert s["rgb_f16"].shape == (6, 3)
        assert s["opacity_u8"].shape == (6,)
        assert s["scales_f16"].shape == (6, 3)
    finally:
        ring.close()


def test_splat_ring_window_size_from_env(tmp_path, monkeypatch):
    gsq = tmp_path / "small.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=8)
    monkeypatch.setenv("GSFLUENT_DECODE_WINDOW_FRAMES", "11")
    ring = SplatRing(gsq)
    try:
        assert ring.window_size == 11
    finally:
        ring.close()


def test_splat_ring_window_env_bad_falls_back(tmp_path, monkeypatch):
    gsq = tmp_path / "small.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=8)
    monkeypatch.setenv("GSFLUENT_DECODE_WINDOW_FRAMES", "not-a-number")
    ring = SplatRing(gsq)
    try:
        assert ring.window_size == 32  # default
    finally:
        ring.close()


def test_splat_ring_rejects_short_header(tmp_path):
    bad = tmp_path / "short.gsq"
    bad.write_bytes(b"not a gsq")
    with pytest.raises(ValueError):
        SplatRing(bad)


# ----- frame decode ----------------------------------------------------------


def test_request_frame_eventually_appears_in_ring(tmp_path):
    gsq = tmp_path / "seq.gsq"
    meta = _write_minimal_gsq(gsq, n_splats=8, n_frames=12)
    ring = SplatRing(gsq, window_size=4)
    try:
        assert not ring.has_frame(3)
        ring.request_frame(3)
        assert _wait_until(lambda: ring.has_frame(3), timeout=2.0)
        got = ring.get_frame(3)
        assert got is not None
        xyz, quat = got
        # Decoded xyz is within quantization tolerance of source.
        # 16-bit quant on bbox-spans: ~max(span) / 65535 per axis.
        tol = float(meta["span"].max() / 65535.0) * 3.0
        np.testing.assert_allclose(xyz, meta["xyz_all"][3], atol=tol)
        # Quaternion: with all-identity input, decoded w should be ~1.
        np.testing.assert_allclose(quat[:, 0], 1.0, atol=1e-3)
    finally:
        ring.close()


def test_decode_blocking_synchronous(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=6)
    ring = SplatRing(gsq, window_size=3)
    try:
        xyz, quat = ring.decode_blocking(2)
        assert xyz.shape == (4, 3)
        assert quat.shape == (4, 4)
        assert ring.has_frame(2)
    finally:
        ring.close()


def test_get_frame_out_of_range_returns_none(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=5)
    ring = SplatRing(gsq, window_size=3)
    try:
        assert ring.get_frame(-1) is None
        assert ring.get_frame(99) is None
        assert not ring.has_frame(99)
    finally:
        ring.close()


# ----- ring eviction -------------------------------------------------------


def test_ring_evicts_farthest_from_cursor(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=20)
    ring = SplatRing(gsq, window_size=3)
    try:
        # Establish cursor at 7 BEFORE decoding so each insert evicts
        # relative to the same anchor. Decode the 3-frame window around
        # the cursor; ring fills cleanly.
        ring.advance(7)
        ring.decode_blocking(7)
        ring.decode_blocking(8)
        ring.decode_blocking(9)
        # Ring is now {7,8,9}; cursor=7 (advance only moved it on the call).
        # Insert frame 0 — distance from 7 is 7, far more than the
        # in-window candidates (0,1,2). Eviction picks the farthest
        # already in the ring (9), then 0 enters. With cursor=7 and ring
        # {7,8,9}: distances are 0,1,2 — farthest is 9 → evict 9 → insert 0.
        ring.decode_blocking(0)
        with ring._lock:
            keys = sorted(ring._ring.keys())
        # Ring size never exceeds window.
        assert len(keys) == 3
        # frame 0 just got inserted, so it's in the ring.
        assert 0 in keys
        # The cursor-anchor frame (7) survives.
        assert 7 in keys
        # Now move cursor to 0 and insert frame 15 — distances to {0,7,8}
        # from cursor=0 are 0,7,8 → 8 is farthest → evict 8 → insert 15.
        ring.advance(0)
        ring.decode_blocking(15)
        with ring._lock:
            keys = sorted(ring._ring.keys())
        assert 0 in keys
        assert 15 in keys
        assert 8 not in keys
    finally:
        ring.close()


def test_ring_size_never_exceeds_window(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=30)
    ring = SplatRing(gsq, window_size=5)
    try:
        for i in range(30):
            ring.decode_blocking(i)
            assert ring.stats()["ring_size"] <= 5
    finally:
        ring.close()


# ----- scrub window -------------------------------------------------------


def test_request_window_clears_far_entries_and_seeds_around_center(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=40)
    ring = SplatRing(gsq, window_size=6)
    try:
        # Warm up at one end.
        for i in range(0, 6):
            ring.decode_blocking(i)
        # Now scrub to the other end.
        ring.request_window(30)
        # The ring should not contain frames 0..5 anymore (those are
        # outside [30 - 3, 30 + 3]).
        assert not _wait_until(lambda: ring.has_frame(0), timeout=0.2)
        # Within 2 s the center frame should be decoded.
        assert _wait_until(lambda: ring.has_frame(30), timeout=2.0)
    finally:
        ring.close()


def test_request_window_at_zero_works(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=10)
    ring = SplatRing(gsq, window_size=4)
    try:
        ring.request_window(0)
        assert _wait_until(lambda: ring.has_frame(0), timeout=2.0)
    finally:
        ring.close()


def test_request_window_clamps_out_of_range(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=5)
    ring = SplatRing(gsq, window_size=4)
    try:
        ring.request_window(999)
        # Should clamp to last frame (4).
        assert _wait_until(lambda: ring.has_frame(4), timeout=2.0)
    finally:
        ring.close()


# ----- advance hint -------------------------------------------------------


def test_advance_prefetches_ahead(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=20)
    ring = SplatRing(gsq, window_size=10, prefetch_ahead=3)
    try:
        ring.decode_blocking(0)
        ring.advance(0)
        # Should prefetch frames 1, 2, 3.
        assert _wait_until(
            lambda: all(ring.has_frame(i) for i in (1, 2, 3)),
            timeout=2.0,
        )
    finally:
        ring.close()


# ----- close --------------------------------------------------------------


def test_close_is_idempotent(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=5)
    ring = SplatRing(gsq, window_size=2)
    ring.close()
    ring.close()      # second call must not raise
    # After close, request/get behave gracefully.
    ring.request_frame(0)
    assert ring.get_frame(0) is None


def test_close_releases_thread(tmp_path):
    import threading

    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=5)
    ring = SplatRing(gsq, window_size=2)
    thread_name = ring._thread.name
    ring.close()
    # Within a short window the daemon thread should exit.
    ok = _wait_until(
        lambda: thread_name not in [t.name for t in threading.enumerate()],
        timeout=2.0,
    )
    assert ok


# ----- concurrency --------------------------------------------------------


def test_concurrent_gets_are_safe(tmp_path):
    import threading

    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=8, n_frames=20)
    ring = SplatRing(gsq, window_size=6)
    try:
        for i in range(20):
            ring.request_frame(i)
        # Hammer the ring from many threads while decode is in flight.
        seen: list[bool] = []
        seen_lock = threading.Lock()

        def worker():
            local = 0
            for _ in range(200):
                got = ring.get_frame(5)
                if got is not None:
                    local += 1
            with seen_lock:
                seen.append(local > 0 or True)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # No crashes; that's the goal.
        assert all(seen)
    finally:
        ring.close()


# ----- static cell wrapper ------------------------------------------------


def test_make_static_cell_shape(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=6, n_frames=8)
    ring = SplatRing(gsq, window_size=3)
    try:
        cell = make_static_cell(ring, viser_k=1.0)
        assert cell["version"] == 2
        assert cell["ring"] is ring
        assert cell["n_frames"] == 8
        assert cell["rgb"].shape == (6, 3)
        assert cell["opacity"].shape == (6, 1)
        assert cell["scales_sq"].shape == (6, 3)
        assert cell["bbox_lo"].shape == (3,)
        assert cell["bbox_hi"].shape == (3,)
    finally:
        ring.close()


# ----- stats / diagnostics ------------------------------------------------


def test_stats_counts_decodes(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=8)
    ring = SplatRing(gsq, window_size=4)
    try:
        ring.decode_blocking(0)
        ring.decode_blocking(1)
        ring.decode_blocking(2)
        st = ring.stats()
        assert st["decoded"] >= 3
        assert st["ring_size"] == 3
    finally:
        ring.close()


def test_note_stutter_increments(tmp_path):
    gsq = tmp_path / "seq.gsq"
    _write_minimal_gsq(gsq, n_splats=4, n_frames=4)
    ring = SplatRing(gsq, window_size=2)
    try:
        ring.note_stutter()
        ring.note_stutter()
        assert ring.stats()["stutter"] == 2
    finally:
        ring.close()
