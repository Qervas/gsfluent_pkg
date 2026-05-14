"""Read fused frame plys + emit binary xyz blobs over WebSocket.

Each frame_*.ply file is a full 3DGS reconstruction at one timestep:
- xyz positions (animate per-frame)
- per-point covariances (constant across frames; sent once)
- per-point RGB (constant; sent once via SH band-0 reconstruction)
- per-point opacity (constant; sent once via sigmoid)

Coordinate convention: all stored frames are Z-up at rest (workbench
invariant — see `core/coord_convert.py` for the import-time rotation
and `tools/fuse_to_full_ply.py` for the sim-time rotation). The
display pipeline therefore reads positions and quaternions through
without any further rotation; the React Three Fiber scene is also
Z-up, so the bytes that go on the wire match the bytes on disk.

Two on-disk formats for the per-frame xyz stream:
- LEGACY: one `frame_NNNN.ply` per frame in the `frames/` subdir.
  Slow (~150 MB per ply for 683k splats), simple, what the fuse
  pipeline emits by default.
- PACKED: a single `frames.bin` next to the `frames/` dir, written
  by `tools/pack_sequence.py`. int16-quantized xyz per frame, one
  global bbox, header described in `pack_sequence.py`. ~30× smaller
  than the legacy layout on disk; sub-mm precision.

This module supports BOTH. `PackedReader` opens a frames.bin via mmap
and exposes per-frame fp32 xyz slices. Callers that need to choose
between layouts use `PackedReader.maybe_open(seq_dir)` — returns None
if the sequence isn't packed.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from plyfile import PlyData

# 0th-order spherical harmonic coefficient for diffuse-color reconstruction.
_SH_C0 = 0.28209479177387814


def parse_frame_xyz(ply_path: Path) -> np.ndarray:
    """Returns (n, 3) float32 xyz, straight from disk.

    Stored data is Z-up (workbench invariant), so no display-time
    rotation is applied. Allocation-light: a single np.stack."""
    v = PlyData.read(str(ply_path))["vertex"].data
    return np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float32)


def parse_static_attrs(ply_path: Path) -> dict | None:
    """Read the per-point attrs that are constant across frames.

    Returns a dict { R: (n,3,3) float32, scales: (n,3) float32,
    rgb: (n,3) float32 in [0,1], opacity: (n,) float32, n: int }
    or None if the ply doesn't carry the full 3DGS attribute set.

    R is the per-gaussian rotation matrix derived from the stored
    quaternion. Stored data is Z-up at rest, so no extra basis
    rotation is composed in here."""
    v = PlyData.read(str(ply_path))["vertex"].data
    needed = (
        "scale_0", "scale_1", "scale_2",
        "rot_0", "rot_1", "rot_2", "rot_3",
        "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
    )
    if not all(k in v.dtype.names for k in needed):
        return None
    n = v.shape[0]
    scales = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=1)).astype(np.float32)
    quats = np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1).astype(np.float32)
    norms = np.linalg.norm(quats, axis=1, keepdims=True)
    # Replace zero, NaN, and inf norms with 1.0 so the divide is safe and
    # produces a sensible (identity-like) rotation rather than NaN.
    bad = ~np.isfinite(norms) | (norms == 0)
    norms[bad] = 1.0
    # Also zero-out any NaN/inf inside the input quat itself before the
    # divide, so we don't propagate NaN through the matrix build.
    quats = np.nan_to_num(quats, nan=0.0, posinf=0.0, neginf=0.0)
    quats /= norms
    qw, qx, qy, qz = quats.T
    R = np.empty((n, 3, 3), dtype=np.float32)
    R[:, 0, 0] = 1 - 2 * (qy * qy + qz * qz);  R[:, 0, 1] = 2 * (qx * qy - qz * qw);  R[:, 0, 2] = 2 * (qx * qz + qy * qw)
    R[:, 1, 0] = 2 * (qx * qy + qz * qw);      R[:, 1, 1] = 1 - 2 * (qx * qx + qz * qz);  R[:, 1, 2] = 2 * (qy * qz - qx * qw)
    R[:, 2, 0] = 2 * (qx * qz - qy * qw);      R[:, 2, 1] = 2 * (qy * qz + qx * qw);      R[:, 2, 2] = 1 - 2 * (qx * qx + qy * qy)
    rgb = np.clip(
        np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1) * _SH_C0 + 0.5,
        0, 1
    ).astype(np.float32)
    op = (1.0 / (1.0 + np.exp(-v["opacity"].astype(np.float32)))).astype(np.float32)
    return {"R": R, "scales": scales, "rgb": rgb, "opacity": op, "n": n}


# -------------------------------------------------------------------- packed
#
# `frames.bin` format (see tools/pack_sequence.py for the full spec):
#
#   offset  size  field
#   ------  ----  --------------------------------------------------
#   0       4     magic ("GSSQ")
#   4       4     u32 version
#   8       4     u32 n_splats
#   12      4     u32 n_frames
#   16      24    6 × fp32 bbox: xmin, ymin, zmin, xmax, ymax, zmax
#   40      ...   n_frames × n_splats × 3 × int16 xyz
#
# Dequantization:
#   norm = (q + 32768) / 65535         # [0, 1]
#   xyz  = norm * (hi - lo) + lo       # fp32 world coords
#
_PACKED_MAGIC = b"GSSQ"
_PACKED_HEADER_FMT = "<4sIII6f"   # magic, version, n_splats, n_frames, 6×fp32 bbox
_PACKED_HEADER_SIZE = struct.calcsize(_PACKED_HEADER_FMT)


class PackedReader:
    """Memory-mapped reader for a sequence's frames.bin.

    Open once per WS connection; index per frame. Thread-safe for reads
    (mmap returns a fresh np.ndarray view per call). Closing is best-effort
    on GC; explicit close() also available if needed."""

    __slots__ = ("path", "version", "n_splats", "n_frames", "bbox_lo", "bbox_hi",
                 "_fh", "_mm", "_body", "_inv_q")

    def __init__(self, path: Path) -> None:
        self.path = path
        # mmap the whole file once. Keep the file handle on `self` so it
        # outlives the mmap — closing the handle before mmap is built
        # yields `Bad file descriptor`. Numpy slicing on the underlying
        # buffer is zero-copy for reads.
        import mmap
        self._fh = path.open("rb")
        self._mm = mmap.mmap(self._fh.fileno(), 0, prot=mmap.PROT_READ)
        magic, version, n_splats, n_frames, *bbox = struct.unpack(
            _PACKED_HEADER_FMT, self._mm[:_PACKED_HEADER_SIZE]
        )
        if magic != _PACKED_MAGIC:
            raise ValueError(f"bad magic {magic!r} in {path}")
        self.version = int(version)
        self.n_splats = int(n_splats)
        self.n_frames = int(n_frames)
        self.bbox_lo = np.array(bbox[0:3], dtype=np.float32)
        self.bbox_hi = np.array(bbox[3:6], dtype=np.float32)
        # body[frame_idx, splat_idx, axis] -> int16
        body_bytes = (n_frames * n_splats * 3 * 2)
        body_arr = np.frombuffer(self._mm, dtype=np.int16,
                                 count=n_frames * n_splats * 3,
                                 offset=_PACKED_HEADER_SIZE)
        self._body = body_arr.reshape(n_frames, n_splats, 3)
        # Pre-compute the dequant scale so the hot path is one mul + one add.
        self._inv_q = (self.bbox_hi - self.bbox_lo).astype(np.float32) / 65535.0

    @classmethod
    def maybe_open(cls, seq_dir: Path) -> "PackedReader | None":
        """Open `<seq_dir>/frames.bin` if it exists; else return None.
        Callers fall back to per-frame ply reads on None."""
        p = seq_dir / "frames.bin"
        if not p.is_file():
            return None
        try:
            return cls(p)
        except (OSError, ValueError):
            return None

    def xyz(self, frame_idx: int) -> np.ndarray:
        """Dequantize one frame to fp32 (n_splats, 3). Allocates per call."""
        if not (0 <= frame_idx < self.n_frames):
            raise IndexError(f"frame {frame_idx} out of [0, {self.n_frames})")
        q = self._body[frame_idx]
        # (q + 32768) * inv_q + lo
        return (q.astype(np.float32) + 32768.0) * self._inv_q + self.bbox_lo

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
