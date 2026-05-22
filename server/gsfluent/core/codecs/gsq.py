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
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import BinaryIO

import numpy as np
import zstandard as zstd

from gsfluent.protocols.cache import (
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
    DecodedFrame,
    SplatFrame,
)
from gsfluent.protocols.observability import EventEmitter

SH_C0 = 0.28209479177387814

MAGIC = b"GSQ1"
VERSION = 1
HEADER_SIZE = 80
INDEX_ENTRY_SIZE = 16
ZSTD_LEVEL = 9
_FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))  # ~7.81e-3


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

        frame_chunks: list[bytes] = []
        for t in range(n_frames):
            raw = xyz_q[t].tobytes() + quat_q[t].tobytes()
            frame_chunks.append(cctx.compress(raw))

        static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
        static_size = len(static_compressed)
        frame0_offset = static_offset + static_size

        index_entries = []
        off = frame0_offset
        for c in frame_chunks:
            index_entries.append((off, len(c)))
            off += len(c)

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
                f.write(b"\x00" * 24)
                assert f.tell() == HEADER_SIZE, f"header drift: {f.tell()}"
                for off, sz in index_entries:
                    f.write(struct.pack("<QII", off, sz, 0))
                assert f.tell() == static_offset, "static offset drift"
                f.write(static_compressed)
                for c in frame_chunks:
                    f.write(c)
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

        frame_chunks = [cctx.compress(xyz_q[t].tobytes() + quat_q[t].tobytes())
                        for t in range(n_frames)]

        static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
        static_size = len(static_compressed)
        frame0_offset = static_offset + static_size

        out.write(MAGIC)
        out.write(struct.pack("<III", VERSION, n_splats, n_frames))
        out.write(struct.pack("<f", 24.0))
        out.write(bbox_min.tobytes())
        out.write(bbox_max.tobytes())
        out.write(struct.pack("<QI", static_offset, static_size))
        out.write(b"\x00" * 24)

        off = frame0_offset
        for c in frame_chunks:
            out.write(struct.pack("<QII", off, len(c), 0))
            off += len(c)
        out.write(static_compressed)
        for c in frame_chunks:
            out.write(c)

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

    async def decode_streaming(
        self, src: AsyncIterator[bytes]
    ) -> AsyncIterator[DecodedFrame]:
        """Streaming decode is the viser_headless client's job today.

        Phase 2 leaves this as a thin pass-through that buffers and then yields
        decoded frames from `decode_all`. Frontend-side streaming decode lives
        in frontend/python/viser_headless.py and stays there for now (the
        Storage layer fronts the bytes via get_range). Returning a buffered
        iterator here is sufficient for backend callers that don't need
        first-frame-fast latency.
        """
        chunks: list[bytes] = []
        async for c in src:
            chunks.append(c)
        body = b"".join(chunks)

        async def _gen():
            for frame in self.decode_all(_BytesReader(body)):
                yield frame
        return _gen()

    def decode_all(self, src: BinaryIO) -> Sequence[DecodedFrame]:
        """Synchronous all-at-once loader. Returns a list of DecodedFrame.

        Reads the .gsq header to find frame offsets, then decompresses each
        frame chunk and the static block. Returns frames with `data` carrying
        the decompressed numpy arrays (xyz_q, quat_q, rgb, opacity, scales).
        """
        header = src.read(HEADER_SIZE)
        if header[:4] != MAGIC:
            raise CodecError(f"bad magic: {header[:4]!r}; expected {MAGIC!r}")
        version, n_splats, n_frames = struct.unpack("<III", header[4:16])
        if version != VERSION:
            raise CodecError(f"unsupported gsq version: {version}")
        # bbox is at offset 20..44 (3 floats min + 3 floats max).
        bbox_min = np.frombuffer(header[20:32], dtype=np.float32)
        bbox_max = np.frombuffer(header[32:44], dtype=np.float32)
        static_offset, static_size = struct.unpack("<QI", header[44:56])

        # Index entries
        index_raw = src.read(n_frames * INDEX_ENTRY_SIZE)
        entries: list[tuple[int, int]] = []
        for i in range(n_frames):
            base = i * INDEX_ENTRY_SIZE
            off, sz, _flags = struct.unpack("<QII", index_raw[base:base + INDEX_ENTRY_SIZE])
            entries.append((off, sz))

        # Static block
        static_compressed = src.read(static_size)
        dctx = zstd.ZstdDecompressor()
        static_uncompressed = dctx.decompress(static_compressed)
        rgb_bytes = static_uncompressed[:n_splats * 3 * 2]
        opacity_bytes = static_uncompressed[n_splats * 3 * 2:n_splats * 3 * 2 + n_splats]
        scales_bytes = static_uncompressed[n_splats * 3 * 2 + n_splats:]
        rgb = np.frombuffer(rgb_bytes, dtype=np.float16).reshape(n_splats, 3)
        opacity = np.frombuffer(opacity_bytes, dtype=np.uint8)
        scales = np.frombuffer(scales_bytes, dtype=np.float16).reshape(n_splats, 3)

        frames_out: list[DecodedFrame] = []
        for i, (_off, sz) in enumerate(entries):
            chunk = src.read(sz)
            raw = dctx.decompress(chunk)
            xyz_q = np.frombuffer(raw[:n_splats * 3 * 2], dtype=np.int16).reshape(n_splats, 3)
            quat_q = np.frombuffer(raw[n_splats * 3 * 2:], dtype=np.int16).reshape(n_splats, 3)
            frames_out.append(DecodedFrame(
                frame_index=i,
                data={
                    "xyz_q": xyz_q,
                    "quat_q": quat_q,
                    "bbox_min": bbox_min,
                    "bbox_max": bbox_max,
                    "rgb": rgb if i == 0 else None,
                    "opacity": opacity if i == 0 else None,
                    "scales": scales if i == 0 else None,
                },
            ))
        return frames_out


class _BytesReader:
    """Minimal BinaryIO-ish wrapper for use by decode_streaming."""
    def __init__(self, body: bytes) -> None:
        self._body = body
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._body[self._pos:]
            self._pos = len(self._body)
            return chunk
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk
