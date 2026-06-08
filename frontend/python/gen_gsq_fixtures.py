"""Emit golden-vector fixtures for the TS .gsq decoder.

Encodes synthetic frames with the REAL GSQCodec, then decodes the expected
outputs with SplatRing (the reference the TS decoder mirrors). Run from the
repo root with the server package importable:

    PYTHONPATH=server python frontend/python/gen_gsq_fixtures.py

Two fixtures:
  drift/ : 8 splats x 35 frames, small per-frame drift + per-frame z-rotation.
           Keyframes at 0 and 30 (K=30) -> exercises keyframe, deltas,
           sequential fast-path, and a keyframe-walk for frames > 30.
  wrap/  : 8 splats x 3 frames at bbox extremes -> deltas overflow int16,
           exercising modular int16 wraparound parity.
"""
import io
import json
import sys
from pathlib import Path

import numpy as np

from gsfluent.core.codecs.gsq import GSQCodec, parse_header_bytes

sys.path.insert(0, str(Path(__file__).resolve().parent))
from splat_ring import SplatRing  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "src" / "lib" / "gsq" / "__fixtures__"


class _NullEmitter:
    def emit(self, *a, **k):
        pass

    def child(self, **k):
        return self


def make_drift(n_splats=8, n_frames=35):
    rng = np.random.default_rng(7)
    base = rng.uniform(-1.0, 1.0, (n_splats, 3)).astype(np.float32)
    frames = []
    for t in range(n_frames):
        xyz = (base + 0.002 * t).astype(np.float32)
        ang = 0.01 * t
        quat = np.zeros((n_splats, 4), dtype=np.float32)
        quat[:, 0] = np.cos(ang / 2.0)   # w
        quat[:, 3] = np.sin(ang / 2.0)   # z  -> [w, x, y, z]
        f = {"xyz": xyz, "quat": quat}
        if t == 0:
            f["rgb"] = np.tile(np.array([0.2, 0.5, 0.8], np.float32), (n_splats, 1))
            f["opacity"] = np.full((n_splats,), 0.9, dtype=np.float32)
            f["scales"] = np.full((n_splats, 3), 0.01, dtype=np.float32)
        frames.append(f)
    return frames


def make_death(n_splats=8, n_frames=20, fly_idx=3, step=0.5):
    """One splat drifts far on +x each frame; with GSFLUENT_GSQ_KILL_RADIUS set
    it crosses the kill radius and gets a finite death_frame, the rest stay
    immortal. Exercises the death-channel parse + cull path."""
    rng = np.random.default_rng(11)
    base = rng.uniform(-0.5, 0.5, (n_splats, 3)).astype(np.float32)
    frames = []
    for t in range(n_frames):
        xyz = base.copy()
        xyz[fly_idx, 0] = base[fly_idx, 0] + step * t
        f = {"xyz": xyz}
        if t == 0:
            f["rgb"] = np.tile(np.array([0.3, 0.6, 0.9], np.float32), (n_splats, 1))
            f["opacity"] = np.full((n_splats,), 0.85, dtype=np.float32)
            f["scales"] = np.full((n_splats, 3), 0.01, dtype=np.float32)
        frames.append(f)
    return frames


def make_wrap(n_splats=8):
    lo = np.full((n_splats, 3), -10.0, dtype=np.float32)
    hi = np.full((n_splats, 3), 10.0, dtype=np.float32)
    seq = [lo, hi, lo]  # delta f1-f0 = +full span (wraps); f2-f1 = -full span
    frames = []
    for t, xyz in enumerate(seq):
        f = {"xyz": xyz.copy()}
        if t == 0:
            f["rgb"] = np.full((n_splats, 3), 0.5, dtype=np.float32)
            f["opacity"] = np.full((n_splats,), 0.8, dtype=np.float32)
            f["scales"] = np.full((n_splats, 3), 0.02, dtype=np.float32)
        frames.append(f)
    return frames


def emit(name, frames):
    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    GSQCodec().encode(frames, buf, _NullEmitter())
    gsq = buf.getvalue()
    (out_dir / "data.gsq").write_bytes(gsq)

    ring = SplatRing(out_dir / "data.gsq")
    st = ring.static
    n, nf = int(st["n_splats"]), int(st["n_frames"])
    (out_dir / "static_rgb.f32").write_bytes(st["rgb_f16"].astype(np.float32).tobytes())
    (out_dir / "static_opacity.f32").write_bytes(
        (st["opacity_u8"].astype(np.float32) / 255.0).tobytes()
    )
    (out_dir / "static_scales.f32").write_bytes(
        st["scales_f16"].astype(np.float32).tobytes()
    )
    for i in range(nf):
        xyz, quat = ring.decode_blocking(i)  # (n,3),(n,4) f32, quat=[w,x,y,z]
        (out_dir / f"frame_{i:03d}_pos.f32").write_bytes(xyz.astype(np.float32).tobytes())
        (out_dir / f"frame_{i:03d}_quat.f32").write_bytes(quat.astype(np.float32).tobytes())
    ring.close()

    h = parse_header_bytes(gsq)
    # Optional death channel: decode the expected per-splat death frames so the
    # TS decoder test can assert parity. null when absent.
    death = None
    if h.get("death_size"):
        import zstandard as _zstd
        raw = _zstd.ZstdDecompressor().decompress(
            gsq[h["death_offset"]: h["death_offset"] + h["death_size"]]
        )
        death = np.frombuffer(raw, dtype=np.uint16).tolist()
    (out_dir / "manifest.json").write_text(json.dumps({
        "name": name, "nSplats": n, "nFrames": nf,
        "fpsHint": float(st["fps_hint"]),
        "bboxMin": st["bbox_min"].tolist(), "bboxMax": st["bbox_max"].tolist(),
        "frameFlags": h["frame_flags"],
        "deathFrame": death,
    }, indent=2))
    print(f"  {name}: {n} splats x {nf} frames -> {out_dir}")


if __name__ == "__main__":
    import os
    print("writing fixtures to", OUT)
    emit("drift", make_drift())
    emit("wrap", make_wrap())
    # death fixture needs the kill radius enabled at encode time.
    os.environ["GSFLUENT_GSQ_KILL_RADIUS"] = "2.0"
    emit("death", make_death())
    os.environ.pop("GSFLUENT_GSQ_KILL_RADIUS", None)
