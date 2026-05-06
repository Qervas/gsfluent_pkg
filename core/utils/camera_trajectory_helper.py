"""
Camera trajectory helper — interpolate between keyframes with cosine ease-in-out.

Keyframe format: list of dicts with "frame", "azim", "elev", "radius".
Returns a (N, 3) array of (azim, elev, radius) per frame.
"""
import math
import numpy as np


def _ease(t: float) -> float:
    """Cosine ease-in-out in [0,1]."""
    return 0.5 - 0.5 * math.cos(math.pi * t)


def build_trajectory(n_frames: int, keyframes: list) -> np.ndarray:
    """
    Build per-frame (azim, elev, radius) by interpolating between keyframes.

    keyframes: [{"frame": int, "azim": float, "elev": float, "radius": float}, ...]
               Must be sorted by "frame". First keyframe's frame should be 0.
               Last keyframe's frame should be >= n_frames - 1.
    """
    kfs = sorted(keyframes, key=lambda k: k["frame"])
    assert kfs[0]["frame"] == 0, "first keyframe must be at frame 0"
    assert kfs[-1]["frame"] >= n_frames - 1, "last keyframe must cover last frame"

    out = np.zeros((n_frames, 3), dtype=np.float32)
    for i in range(n_frames):
        # find segment [k, k+1] containing frame i
        seg = 0
        for j in range(len(kfs) - 1):
            if kfs[j]["frame"] <= i <= kfs[j + 1]["frame"]:
                seg = j
                break
        a, b = kfs[seg], kfs[seg + 1]
        span = max(b["frame"] - a["frame"], 1)
        t = (i - a["frame"]) / span
        s = _ease(t)
        out[i, 0] = a["azim"]   + s * (b["azim"]   - a["azim"])
        out[i, 1] = a["elev"]   + s * (b["elev"]   - a["elev"])
        out[i, 2] = a["radius"] + s * (b["radius"] - a["radius"])
    return out
