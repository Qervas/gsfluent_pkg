"""v2 (.gsq delta) reconstruction in the streaming decode path.

Covers the module-level helper `_v2_apply_payload` that the two streaming
decode loops use to rebuild ABSOLUTE int16 frames from keyframe/delta
payloads, plus that `parse_gsq_header` accepts a v2 buffer and exposes
`frame_flags`.

The streaming loops themselves live inside closures (not unit-testable in
isolation), so the running-absolute reconstruction is extracted into
`_v2_apply_payload` and exercised here directly with the same sequencing the
loops use (payloads arrive strictly in frame order, n_loaded = 0, 1, 2, ...).
"""
import io
import sys
from pathlib import Path

import numpy as np
import zstandard as zstd

_VH = Path(__file__).resolve().parents[2] / "frontend" / "python"
sys.path.insert(0, str(_VH))
import viser_headless as vh  # noqa: E402


def _compress(xyz_i16: np.ndarray, quat_i16: np.ndarray) -> bytes:
    """Build a stored v2 payload: int16 xyz bytes + int16 quat bytes, zstd'd.
    For a keyframe pass absolute arrays; for a delta pass the modular int16
    difference. This mirrors the codec's on-wire payload exactly."""
    return zstd.ZstdCompressor(level=9).compress(
        xyz_i16.astype(np.int16).tobytes() + quat_i16.astype(np.int16).tobytes()
    )


def test_v2_apply_payload_keyframe_returns_absolute():
    n = 5
    xyz = np.random.randint(-32768, 32768, size=(n, 3)).astype(np.int16)
    quat = np.random.randint(-32768, 32768, size=(n, 3)).astype(np.int16)
    blob = _compress(xyz, quat)
    out_xyz, out_quat = vh._v2_apply_payload(None, None, blob, n, is_keyframe=True)
    assert out_xyz.dtype == np.int16 and out_quat.dtype == np.int16
    np.testing.assert_array_equal(out_xyz, xyz)
    np.testing.assert_array_equal(out_quat, quat)


def test_v2_apply_payload_delta_adds_to_prev():
    n = 4
    prev_xyz = np.array([[10, 20, 30]] * n, dtype=np.int16)
    prev_quat = np.array([[1, 2, 3]] * n, dtype=np.int16)
    dx = np.array([[5, -5, 1]] * n, dtype=np.int16)
    dq = np.array([[-1, 0, 2]] * n, dtype=np.int16)
    blob = _compress(dx, dq)
    out_xyz, out_quat = vh._v2_apply_payload(prev_xyz, prev_quat, blob, n, is_keyframe=False)
    np.testing.assert_array_equal(out_xyz, (prev_xyz + dx).astype(np.int16))
    np.testing.assert_array_equal(out_quat, (prev_quat + dq).astype(np.int16))


def test_v2_running_reconstruction_with_keyframe_reset_and_wraparound():
    """Feed a sequence the way the streaming loops do — keyframe, deltas, a
    mid-sequence keyframe reset, and a delta that forces modular int16
    wraparound — and assert the running absolute reconstruction is bit-exact
    against an independently computed ground truth."""
    n = 3
    rng = np.random.default_rng(0)

    # Ground-truth absolute int16 frames.
    abs_xyz = [rng.integers(-32768, 32768, size=(n, 3)).astype(np.int16) for _ in range(6)]
    abs_quat = [rng.integers(-32768, 32768, size=(n, 3)).astype(np.int16) for _ in range(6)]

    # Force a wraparound on frame 1's delta: push one component past int16 max.
    abs_xyz[0][0] = np.array([32760, 0, 0], dtype=np.int16)
    abs_xyz[1][0] = np.array([-32766, 0, 0], dtype=np.int16)  # delta = +10 mod 2^16

    # Keyframes at frame 0 and frame 3 (a mid-sequence reset).
    flags = [1, 0, 0, 1, 0, 0]

    payloads = []
    for t in range(6):
        if flags[t] & 1:
            payloads.append(_compress(abs_xyz[t], abs_quat[t]))
        else:
            dx = (abs_xyz[t].astype(np.int16) - abs_xyz[t - 1].astype(np.int16)).astype(np.int16)
            dq = (abs_quat[t].astype(np.int16) - abs_quat[t - 1].astype(np.int16)).astype(np.int16)
            payloads.append(_compress(dx, dq))

    # Replay exactly as the streaming loop does: running state across frames.
    run_x = run_q = None
    for t in range(6):
        run_x, run_q = vh._v2_apply_payload(
            run_x, run_q, payloads[t], n, is_keyframe=bool(flags[t] & 1)
        )
        np.testing.assert_array_equal(run_x, abs_xyz[t], err_msg=f"xyz frame {t}")
        np.testing.assert_array_equal(run_q, abs_quat[t], err_msg=f"quat frame {t}")


def test_decompress_payload_and_dequantize_split_compose():
    """The split helpers must compose to the same float result as the legacy
    `_gsq_dequantize_frame`."""
    n = 6
    xyz_i16 = np.random.randint(-32768, 32768, size=(n, 3)).astype(np.int16)
    quat_i16 = np.random.randint(-32767, 32768, size=(n, 3)).astype(np.int16)
    blob = _compress(xyz_i16, quat_i16)
    bbox_min = np.array([-1.0, -2.0, -3.0], dtype=np.float32)
    span = np.array([2.0, 4.0, 6.0], dtype=np.float32)

    xyz_a, quat_a = vh._gsq_dequantize_frame(blob, n, bbox_min, span)

    dx, dq = vh._gsq_decompress_payload_i16(blob, n)
    np.testing.assert_array_equal(dx, xyz_i16)
    np.testing.assert_array_equal(dq, quat_i16)
    xyz_b, quat_b = vh._gsq_dequantize_i16(dx, dq, bbox_min, span)

    np.testing.assert_array_equal(xyz_a, xyz_b)
    np.testing.assert_array_equal(quat_a, quat_b)


def _build_v2_gsq() -> bytes:
    """Build a small v2 .gsq buffer via the real codec."""
    from gsfluent.core.codecs.gsq import GSQCodec

    class _NullEmitter:
        def emit(self, *a, **k):
            pass

    n = 8
    rng = np.random.default_rng(1)
    frames = []
    for t in range(40):  # > keyframe interval (30) so we get >= 2 keyframes
        f = {"xyz": (rng.standard_normal((n, 3)).astype(np.float32))}
        if t == 0:
            f["rgb"] = np.clip(rng.random((n, 3)), 0, 1).astype(np.float32)
            f["opacity"] = np.clip(rng.random(n), 0, 1).astype(np.float32)
            f["scales"] = (np.abs(rng.standard_normal((n, 3))) + 0.1).astype(np.float32)
        frames.append(f)
    out = io.BytesIO()
    GSQCodec().encode(iter(frames), out, _NullEmitter())
    return out.getvalue()


def test_parse_gsq_header_accepts_v2_and_exposes_flags():
    buf = _build_v2_gsq()
    h = vh.parse_gsq_header(buf)
    assert h["version"] == 2
    assert "frame_flags" in h
    assert len(h["frame_flags"]) == h["n_frames"]
    # frame 0 and the keyframe-interval frame are keyframes (flags bit0 set).
    assert h["frame_flags"][0] & 1
    assert h["frame_flags"][30] & 1
    # An in-between frame is a delta.
    assert not (h["frame_flags"][1] & 1)
    # frame_index stays (off, sz) pairs.
    assert all(len(e) == 2 for e in h["frame_index"])


def test_streaming_reconstruction_matches_codec_decode_all():
    """End-to-end: replay the codec's stored payloads through the streaming
    helper and confirm the running absolute int16 matches decode_all."""
    from gsfluent.core.codecs.gsq import GSQCodec

    buf = _build_v2_gsq()
    h = vh.parse_gsq_header(buf)
    n = h["n_splats"]

    # Ground truth from the codec's own decode.
    decoded = GSQCodec().decode_all(io.BytesIO(buf))

    run_x = run_q = None
    for t in range(h["n_frames"]):
        off, sz = h["frame_index"][t]
        is_kf = bool(h["frame_flags"][t] & 1)
        run_x, run_q = vh._v2_apply_payload(run_x, run_q, buf[off:off + sz], n, is_kf)
        np.testing.assert_array_equal(run_x, decoded[t].data["xyz_q"], err_msg=f"xyz {t}")
        np.testing.assert_array_equal(run_q, decoded[t].data["quat_q"], err_msg=f"quat {t}")
