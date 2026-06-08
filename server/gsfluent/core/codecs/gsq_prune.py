"""Significance-based pruning for .gsq splat sequences.

Drops low-contribution splats so playback (cov compute, depth sort, WS
payload) and file size all shrink proportionally. Frame-safe: significance
is a function of opacity + scale, which are STATIC in .gsq (only xyz/quat
vary per frame), so a pruned splat is insignificant in every frame.

Pruning is done by raw int16 index-slicing of each frame chunk — no
dequantize/re-quantize round-trip, so it is lossless for the kept splats
and the bbox stays valid (kept splats are a subset of the original).
"""
from __future__ import annotations

import struct

import numpy as np
import zstandard as zstd

from gsfluent.core.codecs.gsq import parse_header_bytes, read_frame_payload_raw_i16


def compute_significance(opacity: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """Per-splat significance ≈ opacity-weighted screen footprint.

    Args:
      opacity: (n,) float32 in [0, 1] (already sigmoid-applied, as stored
               in the .gsq static block: opacity_u8 / 255).
      scales:  (n, 3) float32, the per-axis std-devs (exp of log-scales,
               as stored in the static block).

    Returns (n,) float32 significance. Uses opacity × volume (∏ scales),
    the LightGaussian-style "how much does this splat contribute" proxy.
    Volume rewards spatially large splats; opacity rewards visible ones.
    """
    opacity = np.asarray(opacity, dtype=np.float32).reshape(-1)
    scales = np.asarray(scales, dtype=np.float32).reshape(-1, 3)
    volume = scales[:, 0] * scales[:, 1] * scales[:, 2]
    return (opacity * volume).astype(np.float32)


def select_keep_indices(significance: np.ndarray, keep_count: int) -> np.ndarray:
    """Return the indices of the `keep_count` highest-significance splats,
    sorted ascending (so downstream slicing preserves original ordering).

    `keep_count` is clamped to len(significance).
    """
    n = len(significance)
    k = min(keep_count, n)
    # argpartition is O(n) vs full sort; take the top-k, then sort the
    # selected indices ascending for stable downstream slicing.
    top = np.argpartition(significance, n - k)[n - k:]
    return np.sort(top)


def retention_curve(
    significance: np.ndarray,
    retentions: tuple[float, ...] = (0.999, 0.995, 0.99, 0.98, 0.95),
) -> list[dict]:
    """For each target retention R, report how many splats must be kept so
    that the kept splats' summed significance / total significance >= R,
    and the implied prune ratio.

    This is the no-renderer proxy for "no visible loss": keeping 99.5% of
    total opacity×footprint contribution while dropping a large fraction of
    the COUNT means the dropped splats were individually negligible.
    """
    sig = np.asarray(significance, dtype=np.float64)
    n = len(sig)
    total = float(sig.sum())
    order = np.argsort(sig)[::-1]            # descending
    cumsum = np.cumsum(sig[order])
    out = []
    for r in retentions:
        # smallest k such that cumsum[k-1] >= r * total
        need = r * total
        k = int(np.searchsorted(cumsum, need) + 1)
        k = min(k, n)
        out.append({
            "retention": r,
            "keep_count": k,
            "prune_ratio": 1.0 - k / n if n else 0.0,
        })
    return out

_HEADER_SIZE = 80
_INDEX_ENTRY = 16
_ZSTD_LEVEL = 9


def prune_gsq_bytes(raw: bytes, keep: np.ndarray) -> bytes:
    """Return a new .gsq byte buffer keeping only splats at `keep` indices.

    `keep` must be a 1-D int array of original splat indices, sorted ascending.
    Lossless for kept splats: slices raw int16 frame data + raw static bytes
    by `keep`, re-compresses. bbox + fps_hint are preserved (kept splats are
    a subset, so the original bbox still bounds them).
    """
    keep = np.asarray(keep, dtype=np.int64)
    h = parse_header_bytes(raw)
    n_old = h["n_splats"]
    n_frames = h["n_frames"]
    k = len(keep)

    dctx = zstd.ZstdDecompressor()
    cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)

    # --- static block: rgb f16 (n×3×2) ++ opacity u8 (n) ++ scales f16 (n×3×2)
    s_off, s_sz = h["static_offset"], h["static_size"]
    static = dctx.decompress(bytes(raw[s_off:s_off + s_sz]))
    rgb = np.frombuffer(static[: n_old * 3 * 2], dtype=np.float16).reshape(n_old, 3)
    op_start = n_old * 3 * 2
    opacity = np.frombuffer(static[op_start: op_start + n_old], dtype=np.uint8)
    sc_start = op_start + n_old
    scales = np.frombuffer(static[sc_start: sc_start + n_old * 3 * 2], dtype=np.float16).reshape(n_old, 3)
    new_static = rgb[keep].tobytes() + opacity[keep].tobytes() + scales[keep].tobytes()
    new_static_c = cctx.compress(new_static)

    # --- frame chunks: slice raw int16 by keep, preserving per-frame flags
    new_frames_c = []
    new_frame_flags = []
    for fidx in range(n_frames):
        xyz, qxyz, is_keyframe = read_frame_payload_raw_i16(raw, fidx)
        new_chunk = xyz[keep].tobytes() + qxyz[keep].tobytes()
        new_frames_c.append(cctx.compress(new_chunk))
        new_frame_flags.append(1 if is_keyframe else 0)

    # --- reassemble
    static_offset = _HEADER_SIZE + n_frames * _INDEX_ENTRY
    out = bytearray()
    out += b"GSQ1"
    out += struct.pack("<III", h["version"], k, n_frames)
    out += struct.pack("<f", float(h["fps_hint"]))
    out += h["bbox_min"].astype(np.float32).tobytes()
    out += h["bbox_max"].astype(np.float32).tobytes()
    out += struct.pack("<QI", static_offset, len(new_static_c))
    out += b"\x00" * 24
    assert len(out) == _HEADER_SIZE
    off = static_offset + len(new_static_c)
    for c, flag in zip(new_frames_c, new_frame_flags, strict=True):
        out += struct.pack("<QII", off, len(c), flag)
        off += len(c)
    assert len(out) == static_offset
    out += new_static_c
    for c in new_frames_c:
        out += c
    return bytes(out)


def prune_to_count(raw: bytes, keep_count: int) -> bytes:
    """Prune a .gsq byte buffer to the top-`keep_count` most-significant splats.

    Count-based sibling of prune_to_retention; the shared entry point for the
    LOD base-layer CLI and the pack pipeline so the two never drift.

    keep_count must be > 0. keep_count >= n_splats is a no-op that returns the
    original bytes unchanged (nothing to drop).
    """
    if keep_count <= 0:
        raise ValueError(f"keep_count must be > 0, got {keep_count}")

    h = parse_header_bytes(raw)
    n = h["n_splats"]
    if keep_count >= n:
        return raw

    s_off, s_sz = h["static_offset"], h["static_size"]
    static = zstd.ZstdDecompressor().decompress(bytes(raw[s_off:s_off + s_sz]))
    op_start = n * 3 * 2
    opacity = np.frombuffer(static[op_start:op_start + n], dtype=np.uint8).astype(np.float32) / 255.0
    sc_start = op_start + n
    scales = np.frombuffer(
        static[sc_start:sc_start + n * 3 * 2], dtype=np.float16
    ).reshape(n, 3).astype(np.float32)

    sig = compute_significance(opacity, scales)
    keep = select_keep_indices(sig, keep_count)
    return prune_gsq_bytes(raw, keep)


def prune_to_retention(raw: bytes, retention: float) -> bytes:
    """Prune a .gsq byte buffer to the smallest splat set retaining `retention`
    of total significance. Single entry point shared by the CLI and the pack
    pipeline so the two never drift.

    Reads the static block (opacity + scales), computes per-splat significance,
    finds the keep_count for the target retention via `retention_curve`, selects
    the top-significance indices, and slices the file losslessly.

    `retention` must be in (0, 1]. retention >= 1.0 (or a keep_count == n) is a
    no-op that returns the original bytes unchanged.
    """
    if not (0.0 < retention <= 1.0):
        raise ValueError(f"retention must be in (0, 1], got {retention}")

    h = parse_header_bytes(raw)
    n = h["n_splats"]
    s_off, s_sz = h["static_offset"], h["static_size"]
    static = zstd.ZstdDecompressor().decompress(bytes(raw[s_off:s_off + s_sz]))
    op_start = n * 3 * 2
    opacity = np.frombuffer(static[op_start:op_start + n], dtype=np.uint8).astype(np.float32) / 255.0
    sc_start = op_start + n
    scales = np.frombuffer(
        static[sc_start:sc_start + n * 3 * 2], dtype=np.float16
    ).reshape(n, 3).astype(np.float32)

    sig = compute_significance(opacity, scales)
    keep_count = retention_curve(sig, (retention,))[0]["keep_count"]
    if keep_count >= n:
        # Nothing to drop at this retention — return the original unchanged.
        return raw
    keep = select_keep_indices(sig, keep_count)
    return prune_gsq_bytes(raw, keep)
