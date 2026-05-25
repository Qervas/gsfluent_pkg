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

import numpy as np


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
