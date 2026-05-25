"""Tests for server/tools/transcode_gsq.py — v1 -> v2 migration tool.

Fixture strategy: _build_v1_buffer() mirrors the helper in test_gsq_v2.py
exactly (version=1, absolute frames, flags=0) so the fixture is a true v1
file independent of the current encoder's default output version.
"""
from __future__ import annotations

import io
import struct

import numpy as np
import pytest
import zstandard as zstd

from gsfluent.core.codecs.gsq import (
    GSQ_KEYFRAME_INTERVAL,
    HEADER_SIZE,
    INDEX_ENTRY_SIZE,
    MAGIC,
    _quantize_quats,
    _quantize_xyz,
    decode_frame_raw_i16,
    parse_header_bytes,
)
from tools.transcode_gsq import transcode_to_v2


# ---------------------------------------------------------------------------
# V1 fixture builder
# ---------------------------------------------------------------------------

N_SPLATS = 50
N_FRAMES_SHORT = 5   # < one keyframe interval; only keyframe at 0
N_FRAMES_LONG  = 40  # >= two keyframe intervals; keyframes at 0 and 30


def _make_frames(n_frames: int, n_splats: int = N_SPLATS):
    """Synthesize SplatFrame dicts with small per-frame motion."""
    rng = np.random.default_rng(42)
    base = rng.uniform(-1.0, 1.0, (n_splats, 3)).astype(np.float32)
    frames = []
    for t in range(n_frames):
        xyz = (base + 0.001 * t).astype(np.float32)
        f: dict = {"xyz": xyz}
        if t == 0:
            f["rgb"] = np.full((n_splats, 3), 0.5, dtype=np.float32)
            f["opacity"] = np.full((n_splats,), 0.9, dtype=np.float32)
            f["scales"] = np.full((n_splats, 3), 0.01, dtype=np.float32)
        frames.append(f)
    return frames


def _build_v1_buffer(frames) -> bytes:
    """Hand-build a version-1 .gsq buffer: absolute frame chunks, flags = 0.

    This is intentionally kept independent of GSQCodec.encode (which now
    always outputs v2) so the fixture remains a genuine v1 file.
    """
    n_frames = len(frames)
    n_splats = int(frames[0]["xyz"].shape[0])

    xyz_all = np.stack([np.asarray(f["xyz"], dtype=np.float32) for f in frames])
    quat_all = np.empty((n_frames, n_splats, 4), dtype=np.float32)
    quat_all[..., 0] = 1.0
    quat_all[..., 1:] = 0.0

    bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
    bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
    xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
    quat_q = _quantize_quats(quat_all)

    rgb = np.asarray(frames[0]["rgb"], dtype=np.float32).astype(np.float16)
    opacity = np.clip(
        np.round(np.asarray(frames[0]["opacity"], dtype=np.float32) * 255.0), 0, 255
    ).astype(np.uint8)
    scales = np.asarray(frames[0]["scales"], dtype=np.float32).astype(np.float16)

    cctx = zstd.ZstdCompressor(level=9)
    static = cctx.compress(rgb.tobytes() + opacity.tobytes() + scales.tobytes())
    chunks = [
        cctx.compress(xyz_q[t].tobytes() + quat_q[t].tobytes())
        for t in range(n_frames)
    ]

    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    frame0_offset = static_offset + len(static)

    out = io.BytesIO()
    out.write(MAGIC)
    out.write(struct.pack("<III", 1, n_splats, n_frames))  # VERSION = 1
    out.write(struct.pack("<f", 24.0))
    out.write(bbox_min.tobytes())
    out.write(bbox_max.tobytes())
    out.write(struct.pack("<QI", static_offset, len(static)))
    out.write(b"\x00" * 24)
    off = frame0_offset
    for c in chunks:
        out.write(struct.pack("<QII", off, len(c), 0))  # flags always 0 in v1
        off += len(c)
    out.write(static)
    for c in chunks:
        out.write(c)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Test 1: version field becomes 2
# ---------------------------------------------------------------------------


def test_transcode_header_version():
    """Output header must report version 2."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_SHORT))
    assert parse_header_bytes(v1)["version"] == 1, "fixture sanity: should be v1"

    out = transcode_to_v2(v1)
    h = parse_header_bytes(out)
    assert h["version"] == 2


# ---------------------------------------------------------------------------
# Test 2: keyframe flags at correct positions
# ---------------------------------------------------------------------------


def test_transcode_keyframe_flags_short():
    """With N_FRAMES_SHORT < GSQ_KEYFRAME_INTERVAL only frame 0 is a keyframe."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_SHORT))
    out = transcode_to_v2(v1)
    h = parse_header_bytes(out)
    flags = h["frame_flags"]
    assert len(flags) == N_FRAMES_SHORT
    for t in range(N_FRAMES_SHORT):
        expected_kf = (t % GSQ_KEYFRAME_INTERVAL) == 0
        assert bool(flags[t] & 1) == expected_kf, f"frame {t} keyframe flag mismatch"


def test_transcode_keyframe_flags_long():
    """With N_FRAMES_LONG = 40 keyframes must appear at 0 and 30."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_LONG))
    out = transcode_to_v2(v1)
    h = parse_header_bytes(out)
    flags = h["frame_flags"]
    assert len(flags) == N_FRAMES_LONG
    # Explicit checks at boundary positions.
    assert bool(flags[0] & 1) is True,  "frame 0 must be keyframe"
    assert bool(flags[29] & 1) is False, "frame 29 must be delta"
    assert bool(flags[30] & 1) is True,  "frame 30 must be keyframe"
    assert bool(flags[31] & 1) is False, "frame 31 must be delta"


# ---------------------------------------------------------------------------
# Test 3: lossless round-trip — all frames decode bit-exact after transcode
# ---------------------------------------------------------------------------


def test_transcode_lossless_short():
    """All frames decode bit-exact after v1->v2 transcode (short sequence)."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_SHORT))
    out = transcode_to_v2(v1)
    for t in range(N_FRAMES_SHORT):
        xyz_v1, q_v1 = decode_frame_raw_i16(v1, t)
        xyz_v2, q_v2 = decode_frame_raw_i16(out, t)
        np.testing.assert_array_equal(
            xyz_v2, xyz_v1, err_msg=f"xyz mismatch at frame {t}"
        )
        np.testing.assert_array_equal(
            q_v2, q_v1, err_msg=f"qxyz mismatch at frame {t}"
        )


def test_transcode_lossless_long():
    """All 40 frames decode bit-exact after v1->v2 transcode (long sequence)."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_LONG))
    out = transcode_to_v2(v1)
    for t in range(N_FRAMES_LONG):
        xyz_v1, q_v1 = decode_frame_raw_i16(v1, t)
        xyz_v2, q_v2 = decode_frame_raw_i16(out, t)
        np.testing.assert_array_equal(
            xyz_v2, xyz_v1, err_msg=f"xyz mismatch at frame {t}"
        )
        np.testing.assert_array_equal(
            q_v2, q_v1, err_msg=f"qxyz mismatch at frame {t}"
        )


# ---------------------------------------------------------------------------
# Test 4: idempotent — transcoding a v2 file still round-trips bit-exact
# ---------------------------------------------------------------------------


def test_transcode_idempotent_v2_input():
    """transcode_to_v2 on an already-v2 buffer must still decode bit-exact."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_LONG))
    v2a = transcode_to_v2(v1)                # v1 -> v2
    v2b = transcode_to_v2(v2a)              # v2 -> v2 (re-encode)

    assert parse_header_bytes(v2b)["version"] == 2

    for t in range(N_FRAMES_LONG):
        xyz_a, q_a = decode_frame_raw_i16(v2a, t)
        xyz_b, q_b = decode_frame_raw_i16(v2b, t)
        np.testing.assert_array_equal(
            xyz_b, xyz_a, err_msg=f"idempotent xyz mismatch at frame {t}"
        )
        np.testing.assert_array_equal(
            q_b, q_a, err_msg=f"idempotent qxyz mismatch at frame {t}"
        )


# ---------------------------------------------------------------------------
# Test 5: metadata is preserved verbatim
# ---------------------------------------------------------------------------


def test_transcode_preserves_metadata():
    """n_splats, n_frames, fps_hint, bbox_min, bbox_max must survive transcode."""
    v1 = _build_v1_buffer(_make_frames(N_FRAMES_LONG))
    h1 = parse_header_bytes(v1)
    out = transcode_to_v2(v1)
    h2 = parse_header_bytes(out)

    assert h2["n_splats"] == h1["n_splats"]
    assert h2["n_frames"] == h1["n_frames"]
    assert h2["fps_hint"] == pytest.approx(h1["fps_hint"])
    np.testing.assert_array_equal(h2["bbox_min"], h1["bbox_min"])
    np.testing.assert_array_equal(h2["bbox_max"], h1["bbox_max"])
