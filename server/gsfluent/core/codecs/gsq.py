"""GSQ codec — CacheCodec Protocol impl. Visual-lossless streaming format
for splat sequences.

File layout (unchanged from the prior implementation in tools/pack_splats.py):
    header(80B) + frame_index(16B x N) + static_block(zstd) + frame_chunks(zstd)

Per-frame ply field mapping:
    - xyz:     v["x"], v["y"], v["z"]                       — per frame
    - quat:    (rot_0, rot_1, rot_2, rot_3) normalized      — per frame (v2 only)
    - scales:  exp(scale_0, scale_1, scale_2)               — static (frame 0)
    - rgb:     clip(0.5 + 0.282 * f_dc_*, 0, 1)             — static (frame 0)
    - opacity: sigmoid(opacity_raw)                         — static (frame 0)

If frame 0 has no rot_0..3 fields, we fall back to identity quats — viewer
falls back to the static-cov rendering path.
"""
from __future__ import annotations

import struct
import time
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import numpy as np
import zstandard as zstd

from gsfluent.protocols.cache import (
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
    SplatFrame,
)
from gsfluent.protocols.observability import EventEmitter

SH_C0 = 0.28209479177387814

MAGIC = b"GSQ1"
VERSION = 2
HEADER_SIZE = 80
INDEX_ENTRY_SIZE = 16
ZSTD_LEVEL = 9
GSQ_KEYFRAME_INTERVAL = 30
_FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))  # ~7.81e-3


def parse_header_bytes(buf: bytes) -> dict:
    """Parse the 80-byte header + frame index from a .gsq byte buffer.
    Parse the .gsq header (server-side)."""
    if buf[:4] != b"GSQ1":
        raise ValueError(f"not a .gsq: magic={buf[:4]!r}")
    version, n_splats, n_frames = struct.unpack_from("<III", buf, 4)
    (fps_hint,) = struct.unpack_from("<f", buf, 16)
    bbox_min = np.frombuffer(buf[20:32], dtype=np.float32).copy()
    bbox_max = np.frombuffer(buf[32:44], dtype=np.float32).copy()
    static_offset, static_size = struct.unpack_from("<QI", buf, 44)
    # Reserved region (56..80). First 12 bytes are an OPTIONAL death-channel
    # pointer: death_offset (Q@56) + death_size (I@64). death_size == 0 means
    # "no death channel" (older files wrote \x00*24 here, so this is backward
    # compatible — they parse as absent). See encode_sequence_dir / the
    # `death_frame[]` block for the monotonic per-splat visibility cutoff.
    death_offset, death_size = struct.unpack_from("<QI", buf, 56)
    frame_index = []
    frame_flags: list[int] = []
    for i in range(n_frames):
        off, sz, flags = struct.unpack_from("<QII", buf, 80 + i * 16)
        frame_index.append((off, sz))
        frame_flags.append(flags)
    return {
        "version": version, "n_splats": n_splats, "n_frames": n_frames,
        "fps_hint": fps_hint, "bbox_min": bbox_min, "bbox_max": bbox_max,
        "static_offset": static_offset, "static_size": static_size,
        "death_offset": death_offset, "death_size": death_size,
        "frame_index": frame_index, "frame_flags": frame_flags,
    }


# Sentinel death-frame value: a splat with this value never dies (stays visible
# for the whole clip). uint16 max; n_frames is always far below this.
DEATH_NEVER = 0xFFFF


def compute_death_frames(
    xyz_all: np.ndarray, kill_radius: float,
) -> np.ndarray | None:
    """Per-splat monotonic visibility cutoff for "debris dies at the boundary".

    A splat dies at the first frame `t` where its distance from the frame-0
    centroid exceeds `kill_radius * R0`, where R0 is a robust frame-0 radius
    (95th percentile of ‖pos − centroid₀‖ at frame 0). Debris that flies more
    than `kill_radius` building-radii away is treated as having left the scene.

    Args:
        xyz_all: (n_frames, n_splats, 3) float positions (already sanitized).
        kill_radius: K multiple. <= 0 disables the channel (returns None).

    Returns:
        (n_splats,) uint16 death frame per splat (DEATH_NEVER = never), or None
        when disabled. The cutoff is the FIRST exceedance along time, so it is
        monotonic: once dead, the frontend keeps the splat hidden.
    """
    if kill_radius <= 0.0:
        return None
    n_frames, n_splats, _ = xyz_all.shape
    centroid0 = xyz_all[0].mean(axis=0)
    r0 = np.linalg.norm(xyz_all[0] - centroid0, axis=1)
    radius0 = float(np.percentile(r0, 95.0))
    if not np.isfinite(radius0) or radius0 <= 0.0:
        return None
    threshold = kill_radius * radius0
    # (n_frames, n_splats) distance from the frame-0 centroid each frame.
    dist = np.linalg.norm(xyz_all - centroid0[None, None, :], axis=2)
    exceeded = dist > threshold                       # (n_frames, n_splats)
    ever = exceeded.any(axis=0)                        # (n_splats,)
    # argmax returns the FIRST True index along time (0 when none, so gate on
    # `ever`). Splats that never exceed get the never-dies sentinel.
    first = exceeded.argmax(axis=0).astype(np.int64)   # (n_splats,)
    death = np.where(ever, first, DEATH_NEVER)
    death = np.clip(death, 0, DEATH_NEVER).astype(np.uint16)
    return death


def read_frame_payload_raw_i16(
    buf: bytes, frame_idx: int
) -> tuple[np.ndarray, np.ndarray, bool]:
    """Return the STORED per-frame payload as int16 arrays, plus is_keyframe.

    For a keyframe the payload is the absolute frame; for a delta frame it is
    `frame[t] - frame[t-1]` (modular int16). No reconstruction is performed —
    this is the raw stored content. Used by the pruner (slicing commutes with
    deltas) and by reconstruction logic in decode_frame_raw_i16.
    """
    h = parse_header_bytes(buf)
    n = h["n_splats"]
    off, sz = h["frame_index"][frame_idx]
    is_keyframe = bool(h["frame_flags"][frame_idx] & 1)
    raw = zstd.ZstdDecompressor().decompress(bytes(buf[off:off + sz]))
    xyz = np.frombuffer(raw[: n * 3 * 2], dtype=np.int16).reshape(n, 3)
    qxyz = np.frombuffer(raw[n * 3 * 2 : n * 3 * 2 * 2], dtype=np.int16).reshape(n, 3)
    return xyz, qxyz, is_keyframe


def decode_frame_raw_i16(buf: bytes, frame_idx: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (xyz_i16 (n,3), qxyz_i16 (n,3)) ABSOLUTE int16 arrays for a frame
    — no dequantization. Used by the pruner to slice losslessly.

    v1: each stored chunk is already absolute.
    v2: scan backward to the nearest keyframe (flags bit0 set) <= frame_idx,
    then accumulate stored deltas with modular int16 add up to frame_idx.
    """
    h = parse_header_bytes(buf)
    version = h["version"]
    if version == 1:
        n = h["n_splats"]
        off, sz = h["frame_index"][frame_idx]
        raw = zstd.ZstdDecompressor().decompress(bytes(buf[off:off + sz]))
        xyz = np.frombuffer(raw[: n * 3 * 2], dtype=np.int16).reshape(n, 3)
        qxyz = np.frombuffer(
            raw[n * 3 * 2 : n * 3 * 2 * 2], dtype=np.int16
        ).reshape(n, 3)
        return xyz, qxyz

    # v2: reconstruct absolute from the nearest keyframe.
    flags = h["frame_flags"]
    kf = frame_idx
    while kf > 0 and not (flags[kf] & 1):
        kf -= 1
    xyz_acc, q_acc, _is_kf = read_frame_payload_raw_i16(buf, kf)
    xyz_acc = xyz_acc.copy()
    q_acc = q_acc.copy()
    for i in range(kf + 1, frame_idx + 1):
        dx, dq, _ = read_frame_payload_raw_i16(buf, i)
        xyz_acc = (xyz_acc + dx).astype(np.int16)
        q_acc = (q_acc + dq).astype(np.int16)
    return xyz_acc, q_acc


# ---- helper functions copied verbatim from tools/pack_splats.py ------------


def _has_rot_fields(v) -> bool:
    return all(f in v.dtype.names for f in ("rot_0", "rot_1", "rot_2", "rot_3"))


def _norm_quats(qw, qx, qy, qz):
    """Normalize + fix sign so scalar is non-negative (continuous trajectory)."""
    qn = np.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    qn[qn == 0] = 1.0
    qw, qx, qy, qz = qw / qn, qx / qn, qy / qn, qz / qn
    flip = qw < 0
    qw[flip] = -qw[flip]
    qx[flip] = -qx[flip]
    qy[flip] = -qy[flip]
    qz[flip] = -qz[flip]
    return qw, qx, qy, qz


def _read_static_attrs(v0, on_event: EventEmitter):
    sx = np.exp(np.asarray(v0["scale_0"], dtype=np.float32))
    sy = np.exp(np.asarray(v0["scale_1"], dtype=np.float32))
    sz = np.exp(np.asarray(v0["scale_2"], dtype=np.float32))
    scales = np.stack([sx, sy, sz], axis=1)
    n_clamped = int((scales < _FP16_COV_FLOOR_SQRT).any(axis=1).sum())
    if n_clamped:
        on_event.emit(
            "encode.scales_clamped",
            n_clamped=n_clamped,
            n_total=len(scales),
            pct=n_clamped / len(scales) * 100,
        )
        np.maximum(scales, _FP16_COV_FLOOR_SQRT, out=scales)

    rgb = np.stack([
        0.5 + np.asarray(v0["f_dc_0"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_1"], dtype=np.float32) * SH_C0,
        0.5 + np.asarray(v0["f_dc_2"], dtype=np.float32) * SH_C0,
    ], axis=1).astype(np.float32)

    op_logit = np.asarray(v0["opacity"], dtype=np.float32)
    opacity = (1.0 / (1.0 + np.exp(-op_logit))).astype(np.float32)
    return scales, rgb, opacity


def _read_per_frame(v, want_quats: bool):
    xyz = np.stack([
        np.asarray(v["x"], dtype=np.float32),
        np.asarray(v["y"], dtype=np.float32),
        np.asarray(v["z"], dtype=np.float32),
    ], axis=1)
    quat = None
    if want_quats:
        qw = np.asarray(v["rot_0"], dtype=np.float32)
        qx = np.asarray(v["rot_1"], dtype=np.float32)
        qy = np.asarray(v["rot_2"], dtype=np.float32)
        qz = np.asarray(v["rot_3"], dtype=np.float32)
        qw, qx, qy, qz = _norm_quats(qw, qx, qy, qz)
        quat = np.stack([qw, qx, qy, qz], axis=1)
    return xyz, quat


def _quantize_xyz(xyz, bmin, bmax):
    span = (bmax - bmin).astype(np.float64)
    span = np.where(span > 0, span, 1.0)
    q = (xyz.astype(np.float64) - bmin) / span * 65535.0
    q = np.clip(np.round(q), 0, 65535).astype(np.int32) - 32768
    return q.astype(np.int16)


def _quantize_quats(q):
    qxyz = np.clip(q[..., 1:4], -1.0, 1.0)
    return np.round(qxyz * 32767.0).astype(np.int16)


def _v2_frame_payloads(xyz_q, quat_q, cctx, K=GSQ_KEYFRAME_INTERVAL):
    """Build v2 per-frame compressed payloads + flags.

    Frame 0 and every K-th frame are keyframes (absolute int16). All other
    frames store modular int16 deltas from the previous frame. Returns
    (payloads: list[bytes], flags: list[int]) where flags bit0 = is_keyframe.
    """
    T = xyz_q.shape[0]
    payloads: list[bytes] = []
    flags: list[int] = []
    for t in range(T):
        kf = (t % K == 0)
        x = xyz_q[t] if kf else (xyz_q[t] - xyz_q[t - 1]).astype(np.int16)
        q = quat_q[t] if kf else (quat_q[t] - quat_q[t - 1]).astype(np.int16)
        payloads.append(cctx.compress(x.tobytes() + q.tobytes()))
        flags.append(1 if kf else 0)
    return payloads, flags


# ---- GSQCodec class -------------------------------------------------------


class GSQCodec:
    """CacheCodec Protocol impl for the .gsq streaming format.

    Two entry points:
      - encode_sequence_dir(frames_dir, out_path, on_event): the canonical
        path used by the run pipeline. Reads frame_*.ply files from a
        directory, sanitizes, encodes, writes the .gsq atomically.
      - encode(frames, out, on_event): the Protocol-required entry point.
        Accepts an iterable of SplatFrame dicts already in memory.

    For now `encode_sequence_dir` is the primary one — the pipeline always
    has plys on disk. The in-memory `encode` is a thin convenience wrapper
    used by tests and any future caller that builds SplatFrames programmatically.
    """

    media_type = "application/x-gsq"
    file_extension = ".gsq"

    def encode_sequence_dir(
        self,
        frames_dir: Path,
        out_path: Path,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        """Read frame_*.ply from `frames_dir` and write a .gsq to `out_path`.

        The encode pipeline (sanitization + quantization + zstd compression
        + atomic write) is copied verbatim from the prior implementation in
        tools/pack_splats.py — behavior is byte-for-byte preserved.
        """
        from plyfile import PlyData

        if not frames_dir.is_dir():
            raise CodecError(f"frames dir does not exist: {frames_dir}")

        frame_paths = sorted(
            p for p in frames_dir.iterdir()
            if p.is_file() and p.name.startswith("frame_") and p.suffix == ".ply"
        )
        if not frame_paths:
            raise CodecError(f"no frame_*.ply in {frames_dir}")

        n_frames = len(frame_paths)
        on_event.emit("encode.started", n_frames=n_frames, source=str(frames_dir))
        t_start = time.time()

        v0 = PlyData.read(str(frame_paths[0]))["vertex"].data
        n_splats = v0.shape[0]
        has_rot_v0 = _has_rot_fields(v0)
        probe = frame_paths[1] if n_frames > 1 else frame_paths[0]
        v_probe = PlyData.read(str(probe))["vertex"].data
        want_quats = has_rot_v0 and _has_rot_fields(v_probe)
        if not has_rot_v0:
            on_event.emit("encode.no_quats", note="frame 0 lacks rot_* fields; using identity")

        scales, rgb, opacity = _read_static_attrs(v0, on_event)

        xyz_all = np.empty((n_frames, n_splats, 3), dtype=np.float32)
        quat_all = np.empty((n_frames, n_splats, 4), dtype=np.float32)
        if not want_quats:
            quat_all[..., 0] = 1.0
            quat_all[..., 1:] = 0.0

        for i, p in enumerate(frame_paths):
            v = PlyData.read(str(p))["vertex"].data
            if v.shape[0] != n_splats:
                raise CodecError(
                    f"frame {p.name} has {v.shape[0]} splats, expected {n_splats}"
                )
            xyz, quat = _read_per_frame(v, want_quats=want_quats)
            xyz_all[i] = xyz
            if quat is not None:
                quat_all[i] = quat

        # Sanitization. Non-finite xyz -> forward-fill; non-finite or zero-norm
        # quats -> identity. Same logic as tools/pack_splats.py.
        bad_xyz = ~np.isfinite(xyz_all).all(axis=2)
        if bad_xyz.any():
            n_bad = int(bad_xyz.sum())
            on_event.emit("encode.sanitize.positions", n_bad=n_bad)
            if bad_xyz[0].any():
                good = ~bad_xyz[0]
                if not good.any():
                    raise CodecUnsanitizableError(
                        "frame 0 has no finite positions; cannot encode"
                    )
                ctr = xyz_all[0][good].mean(axis=0)
                xyz_all[0][bad_xyz[0]] = ctr
            for t in range(1, n_frames):
                b = bad_xyz[t]
                if b.any():
                    xyz_all[t][b] = xyz_all[t - 1][b]

        qn2 = (quat_all * quat_all).sum(axis=-1)
        bad_q = (~np.isfinite(qn2)) | (qn2 < 1e-12)
        if bad_q.any():
            n_bad = int(bad_q.sum())
            on_event.emit("encode.sanitize.quats", n_bad=n_bad)
            quat_all[bad_q] = np.array([1, 0, 0, 0], dtype=np.float32)

        bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
        bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
        if not (np.isfinite(bbox_min).all() and np.isfinite(bbox_max).all()):
            raise CodecUnsanitizableError(
                f"non-finite bbox after sanitization: {bbox_min}..{bbox_max}"
            )

        xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
        quat_q = _quantize_quats(quat_all)

        rgb_f16 = rgb.astype(np.float16)
        opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
        scales_f16 = scales.astype(np.float16)

        cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
        static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
        static_compressed = cctx.compress(static_uncompressed)

        frame_chunks, frame_flags = _v2_frame_payloads(xyz_q, quat_q, cctx)

        # Optional death channel ("debris dies at the boundary"). Disabled by
        # default (kill_radius=0) -> no block, byte-identical to prior output.
        import os
        try:
            kill_radius = float(os.environ.get("GSFLUENT_GSQ_KILL_RADIUS", "0"))
        except ValueError:
            kill_radius = 0.0
        death = compute_death_frames(xyz_all, kill_radius)
        death_compressed = b""
        if death is not None:
            death_compressed = cctx.compress(death.tobytes())
            n_dead = int((death != DEATH_NEVER).sum())
            on_event.emit(
                "encode.death_channel",
                kill_radius=kill_radius,
                n_dead=n_dead,
                n_splats=n_splats,
            )

        static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
        static_size = len(static_compressed)
        frame0_offset = static_offset + static_size

        index_entries = []
        off = frame0_offset
        for c, fl in zip(frame_chunks, frame_flags, strict=True):
            index_entries.append((off, len(c), fl))
            off += len(c)
        # Death block (if any) lands at EOF, after all frame chunks, so every
        # existing offset above is unchanged -> backward compatible.
        death_offset = off if death_compressed else 0
        death_size = len(death_compressed)

        # Atomic write via tmp + replace.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(MAGIC)
                f.write(struct.pack("<III", VERSION, n_splats, n_frames))
                f.write(struct.pack("<f", 24.0))  # fps_hint
                f.write(bbox_min.tobytes())
                f.write(bbox_max.tobytes())
                f.write(struct.pack("<QI", static_offset, static_size))
                # Reserved region (24B): death-channel pointer (12B) + pad (12B).
                f.write(struct.pack("<QI", death_offset, death_size))
                f.write(b"\x00" * 12)
                assert f.tell() == HEADER_SIZE, f"header drift: {f.tell()}"
                for off, sz, fl in index_entries:
                    f.write(struct.pack("<QII", off, sz, fl))
                assert f.tell() == static_offset, "static offset drift"
                f.write(static_compressed)
                for c in frame_chunks:
                    f.write(c)
                if death_compressed:
                    assert f.tell() == death_offset, "death offset drift"
                    f.write(death_compressed)
            import os
            os.replace(str(tmp_path), str(out_path))
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        duration_sec = time.time() - t_start
        out_size = out_path.stat().st_size
        on_event.emit(
            "encode.completed",
            n_frames=n_frames,
            n_splats=n_splats,
            out_bytes=out_size,
            duration_sec=duration_sec,
        )
        return CacheMetadata(
            n_splats=n_splats,
            n_frames=n_frames,
            bbox=(
                float(bbox_min[0]), float(bbox_min[1]), float(bbox_min[2]),
                float(bbox_max[0]), float(bbox_max[1]), float(bbox_max[2]),
            ),
            fps_hint=24.0,
        )

    # ---- CacheCodec Protocol-required methods ----

    def encode(
        self,
        frames: Iterable[SplatFrame],
        out: BinaryIO,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        """In-memory encode entry point — accepts pre-built SplatFrame dicts.

        Phase 2 leaves this minimal: the pipeline always has plys on disk and
        calls encode_sequence_dir. The in-memory path is used by tests of the
        Protocol surface and any future caller that synthesizes frames
        directly. Each frame dict must carry 'xyz' (N, 3) float32; frame 0
        must additionally carry 'scales', 'rgb', 'opacity'; per-frame 'quat'
        is optional (identity if absent).
        """
        frame_list = list(frames)
        if not frame_list:
            raise CodecError("encode() called with empty frame iterable")
        on_event.emit("encode.started", n_frames=len(frame_list), source="<in-memory>")

        n_frames = len(frame_list)
        n_splats = int(frame_list[0]["xyz"].shape[0])

        xyz_all = np.stack([np.asarray(f["xyz"], dtype=np.float32) for f in frame_list])
        quat_all = np.empty((n_frames, n_splats, 4), dtype=np.float32)
        quat_all[..., 0] = 1.0
        quat_all[..., 1:] = 0.0
        for i, f in enumerate(frame_list):
            if "quat" in f and f["quat"] is not None:
                quat_all[i] = np.asarray(f["quat"], dtype=np.float32)

        bbox_min = xyz_all.reshape(-1, 3).min(axis=0).astype(np.float32)
        bbox_max = xyz_all.reshape(-1, 3).max(axis=0).astype(np.float32)
        if not (np.isfinite(bbox_min).all() and np.isfinite(bbox_max).all()):
            raise CodecUnsanitizableError("non-finite bbox in in-memory encode")

        xyz_q = _quantize_xyz(xyz_all, bbox_min, bbox_max)
        quat_q = _quantize_quats(quat_all)

        rgb = np.asarray(frame_list[0]["rgb"], dtype=np.float32)
        opacity = np.asarray(frame_list[0]["opacity"], dtype=np.float32)
        scales = np.asarray(frame_list[0]["scales"], dtype=np.float32)
        rgb_f16 = rgb.astype(np.float16)
        opacity_u8 = np.clip(np.round(opacity * 255.0), 0, 255).astype(np.uint8)
        scales_f16 = scales.astype(np.float16)

        cctx = zstd.ZstdCompressor(level=ZSTD_LEVEL)
        static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()
        static_compressed = cctx.compress(static_uncompressed)

        frame_chunks, frame_flags = _v2_frame_payloads(xyz_q, quat_q, cctx)

        # Optional death channel (see encode_sequence_dir). Disabled by default.
        import os
        try:
            kill_radius = float(os.environ.get("GSFLUENT_GSQ_KILL_RADIUS", "0"))
        except ValueError:
            kill_radius = 0.0
        death = compute_death_frames(xyz_all, kill_radius)
        death_compressed = cctx.compress(death.tobytes()) if death is not None else b""

        static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
        static_size = len(static_compressed)
        frame0_offset = static_offset + static_size

        off = frame0_offset
        index_blob = b""
        for c, fl in zip(frame_chunks, frame_flags, strict=True):
            index_blob += struct.pack("<QII", off, len(c), fl)
            off += len(c)
        death_offset = off if death_compressed else 0
        death_size = len(death_compressed)

        out.write(MAGIC)
        out.write(struct.pack("<III", VERSION, n_splats, n_frames))
        out.write(struct.pack("<f", 24.0))
        out.write(bbox_min.tobytes())
        out.write(bbox_max.tobytes())
        out.write(struct.pack("<QI", static_offset, static_size))
        out.write(struct.pack("<QI", death_offset, death_size))
        out.write(b"\x00" * 12)

        out.write(index_blob)
        out.write(static_compressed)
        for c in frame_chunks:
            out.write(c)
        if death_compressed:
            out.write(death_compressed)

        on_event.emit("encode.completed", n_frames=n_frames, n_splats=n_splats)
        return CacheMetadata(
            n_splats=n_splats,
            n_frames=n_frames,
            bbox=(
                float(bbox_min[0]), float(bbox_min[1]), float(bbox_min[2]),
                float(bbox_max[0]), float(bbox_max[1]), float(bbox_max[2]),
            ),
            fps_hint=24.0,
        )
