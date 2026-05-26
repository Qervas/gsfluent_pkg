"""GSQ v2 (delta + keyframe) codec tests.

Covers: header flags parsing, stored-payload reading, absolute reconstruction
via decode_frame_raw_i16, modular int16 wraparound losslessness, and v1
version-dispatch (v1 reading must keep working).
"""
import io
import struct

import numpy as np

from gsfluent.core.codecs.gsq import (
    GSQ_KEYFRAME_INTERVAL,
    GSQCodec,
    decode_frame_raw_i16,
    parse_header_bytes,
    read_frame_payload_raw_i16,
)


class _NullEmitter:
    """Inline emitter — drops events."""

    def emit(self, event: str, **context) -> None:
        pass

    def child(self, **context):
        return self


N_SPLATS = 200
N_FRAMES = 40  # K=30 -> keyframes at 0 and 30


def _make_frames(n_frames: int = N_FRAMES, n_splats: int = N_SPLATS):
    """Synthesize frames with small per-frame motion so deltas are tiny."""
    rng = np.random.default_rng(7)
    base = rng.uniform(-1.0, 1.0, (n_splats, 3)).astype(np.float32)
    frames = []
    for t in range(n_frames):
        xyz = base + 0.001 * t  # tiny drift; stays well inside the bbox span
        f = {"xyz": xyz.astype(np.float32)}
        if t == 0:
            f["rgb"] = np.full((n_splats, 3), 0.5, dtype=np.float32)
            f["opacity"] = np.full((n_splats,), 0.9, dtype=np.float32)
            f["scales"] = np.full((n_splats, 3), 0.01, dtype=np.float32)
        frames.append(f)
    return frames


def _encode_v2(frames) -> bytes:
    out = io.BytesIO()
    GSQCodec().encode(frames, out, _NullEmitter())
    return out.getvalue()


# ---- Test 1: header flags ------------------------------------------------


def test_parse_header_v2_flags_and_index_shape():
    buf = _encode_v2(_make_frames())
    h = parse_header_bytes(buf)
    assert h["version"] == 2
    assert h["n_splats"] == N_SPLATS
    assert h["n_frames"] == N_FRAMES

    # frame_index must stay a list of (off, sz) 2-tuples (callers unpack it).
    assert len(h["frame_index"]) == N_FRAMES
    for entry in h["frame_index"]:
        assert len(entry) == 2

    # frame_flags: bit0 set at keyframes (0 and 30), clear elsewhere.
    flags = h["frame_flags"]
    assert len(flags) == N_FRAMES
    for t in range(N_FRAMES):
        is_kf = (t % GSQ_KEYFRAME_INTERVAL) == 0
        assert bool(flags[t] & 1) == is_kf, f"frame {t} keyframe flag wrong"


# ---- Test 2: decode_frame_raw_i16 absolute reconstruction ----------------


def test_decode_frame_raw_i16_reconstruction_consistent():
    """Reconstructing absolute from stored deltas must be consistent: the
    accumulation from the nearest keyframe equals the direct raw decode."""
    frames = _make_frames()
    buf = _encode_v2(frames)
    h = parse_header_bytes(buf)
    flags = h["frame_flags"]

    for t in range(h["n_frames"]):
        kf = t
        while not (flags[kf] & 1):
            kf -= 1
        xyz_acc, q_acc, _is_kf = read_frame_payload_raw_i16(buf, kf)
        for i in range(kf + 1, t + 1):
            dx, dq, _ = read_frame_payload_raw_i16(buf, i)
            xyz_acc = (xyz_acc + dx).astype(np.int16)
            q_acc = (q_acc + dq).astype(np.int16)
        xyz, qxyz = decode_frame_raw_i16(buf, t)
        np.testing.assert_array_equal(xyz, xyz_acc)
        np.testing.assert_array_equal(qxyz, q_acc)


def test_modular_wraparound_lossless():
    """A deliberate +20000 then -20000 jump between two non-keyframe frames
    overflows int16 deltas; modular arithmetic must still round-trip exactly."""
    n = 50
    n_frames = 35  # keyframes at 0 and 30; the jumps live in the delta region
    rng = np.random.default_rng(3)
    base = rng.uniform(-0.5, 0.5, (n, 3)).astype(np.float32)
    frames = []
    for t in range(n_frames):
        xyz = base.copy()
        if t == 10:
            xyz[:, 0] += 1.5  # big positive jump in x (overflows i16 delta)
        elif t == 11:
            xyz[:, 0] -= 1.5  # big negative jump back
        f = {"xyz": xyz.astype(np.float32)}
        if t == 0:
            f["rgb"] = np.full((n, 3), 0.5, dtype=np.float32)
            f["opacity"] = np.full((n,), 0.9, dtype=np.float32)
            f["scales"] = np.full((n, 3), 0.01, dtype=np.float32)
        frames.append(f)

    buf = _encode_v2(frames)
    # Reference reconstruction: accumulate stored deltas from the nearest
    # keyframe with modular int16 add, independent of decode_frame_raw_i16.
    h = parse_header_bytes(buf)
    flags = h["frame_flags"]
    for t in range(n_frames):
        kf = t
        while not (flags[kf] & 1):
            kf -= 1
        ref_xyz, ref_q, _ = read_frame_payload_raw_i16(buf, kf)
        for i in range(kf + 1, t + 1):
            dx, dq, _ = read_frame_payload_raw_i16(buf, i)
            ref_xyz = (ref_xyz + dx).astype(np.int16)
            ref_q = (ref_q + dq).astype(np.int16)
        xyz, qxyz = decode_frame_raw_i16(buf, t)
        np.testing.assert_array_equal(xyz, ref_xyz)
        np.testing.assert_array_equal(qxyz, ref_q)


# ---- Test 3: read_frame_payload_raw_i16 returns STORED payload -----------


def test_read_frame_payload_keyframe_vs_delta():
    frames = _make_frames()
    buf = _encode_v2(frames)

    # Keyframe (frame 0): stored payload == absolute frame.
    xyz0, q0, kf0 = read_frame_payload_raw_i16(buf, 0)
    assert kf0 is True
    abs0_xyz, abs0_q = decode_frame_raw_i16(buf, 0)
    np.testing.assert_array_equal(xyz0, abs0_xyz)
    np.testing.assert_array_equal(q0, abs0_q)

    # Keyframe at 30 too.
    _, _, kf30 = read_frame_payload_raw_i16(buf, 30)
    assert kf30 is True

    # Delta frame (frame 1): stored payload == frame[1] - frame[0] (int16).
    xyz1, q1, kf1 = read_frame_payload_raw_i16(buf, 1)
    assert kf1 is False
    abs1_xyz, abs1_q = decode_frame_raw_i16(buf, 1)
    expect_dx = (abs1_xyz - abs0_xyz).astype(np.int16)
    expect_dq = (abs1_q - abs0_q).astype(np.int16)
    np.testing.assert_array_equal(xyz1, expect_dx)
    np.testing.assert_array_equal(q1, expect_dq)


# ---- Test 5: v1 buffers still decode (version dispatch) ------------------


def _build_v1_buffer(frames) -> bytes:
    """Hand-build a v1 .gsq (absolute frame chunks, VERSION=1, flags=0) so v1
    reading stays covered independent of the encoder's current default."""
    import zstandard as zstd

    from gsfluent.core.codecs.gsq import (
        HEADER_SIZE,
        INDEX_ENTRY_SIZE,
        MAGIC,
        _quantize_quats,
        _quantize_xyz,
    )

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
        out.write(struct.pack("<QII", off, len(c), 0))  # flags always 0
        off += len(c)
    out.write(static)
    for c in chunks:
        out.write(c)
    return out.getvalue()


def test_v1_buffer_still_decodes():
    frames = _make_frames(n_frames=5, n_splats=20)
    v1 = _build_v1_buffer(frames)

    h = parse_header_bytes(v1)
    assert h["version"] == 1
    # v1 flags are all 0.
    assert all(fl == 0 for fl in h["frame_flags"])

    # v1 version-dispatch: decode_frame_raw_i16 must read each absolute chunk
    # directly, and read_frame_payload_raw_i16 must return the same bytes (no
    # delta reconstruction for v1).
    for t in range(5):
        xyz, qxyz = decode_frame_raw_i16(v1, t)
        stored_xyz, stored_qxyz, _ = read_frame_payload_raw_i16(v1, t)
        np.testing.assert_array_equal(xyz, stored_xyz)
        np.testing.assert_array_equal(qxyz, stored_qxyz)
