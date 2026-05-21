"""Headless viser splat renderer — controlled entirely via HTTP.

Strips viser's built-in GUI (no internal cell dropdown, no play/pause
button, no frame slider) and exposes a small HTTP control API on a
sidecar port. The React workbench drives everything — sequence
selection, frame index, playback, camera — via that API. Viser is
reduced to "splat renderer service".

Endpoints (port 8092 by default, configurable):
    POST /set         body={"cell": str?, "frame": int?}     advance playback
    POST /camera      body={"position": [x,y,z], "target": [x,y,z]}  align viewport
    GET  /state       → {"cell", "frame", "n_frames", "cells", "bbox": {...}}
    GET  /camera      → {"position": [...], "target": [...], "wxyz": [...]}
    GET  /sync-status → sync_daemon's last status snapshot (verbatim)

The /set endpoint is fire-and-forget; it returns the resolved state but
the actual GPU upload happens on viser's render thread on its next tick.
Latency is whatever viser's WS push + browser render takes (~1 frame).

Usage:
    python frontend/python/viser_headless.py --npz_dir work/cache/viser
"""
from __future__ import annotations

import argparse
import re
import signal
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import uvicorn
import viser
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Strict-allowlist regex for any user-supplied identifier that becomes
# part of a filesystem path. Library sequence names already pass through
# this on the server side; we enforce again here because the client
# might be talking to a hostile or buggy server. Reject anything with
# `..`, `/`, spaces, or shell metas.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


# Workbench dark palette (mirrors frontend/tailwind.config.js). Keeping
# this in sync visually means the iframe inside the React workbench
# doesn't look like a foreign element pasted in. RGB tuples are 0-255.
_CANVAS_RGB    = (10, 15, 26)     # tailwind `canvas`     #0a0f1a
_GRID_CELL_RGB = (33, 38, 45)     # tailwind `border`     #21262d
_ACCENT_RGB    = (34, 211, 238)   # tailwind `accent`     #22d3ee

# No-op constant left in place during a transition: the K scale-up
# used to happen here, but it's now done upstream in
# `frontend/python/fuse_to_full_ply.py` (or `frontend/python/sequence_to_viser_npz.py`)
# so the per-frame plys/npzs already arrive in source-world
# coordinates. Setting K=1 means viser_headless renders whatever is
# in the .npz without rewriting it.
_VISER_K = 1.0


# Default cache location for downloaded model plys. Defaults to a
# repo-relative path so a single deployment owns its cache; override
# with GSFLUENT_MODEL_CACHE_DIR if the repo lives on a small disk and
# you'd rather use /tmp or an XDG cache dir.
_DEFAULT_MODEL_CACHE = (
    Path(__file__).resolve().parents[2] / "work" / "cache" / "model_files"
)


def fetch_model_ply(server_base: str, model_path_on_server: str) -> Path:
    """Download a model's .ply from the server, cache it locally,
    and return the local path.

    Cache key is the absolute path on the server (so collisions are
    impossible across different models). Files persist across viser
    restarts to avoid re-downloading. Configure the cache location
    with GSFLUENT_MODEL_CACHE_DIR; default is work/cache/model_files/
    relative to the repo root.

    Args:
      server_base: e.g. "http://<server>:18080"
      model_path_on_server: absolute path the server knows, e.g.
        "<pkg-root>/work/library/models/<name>"
    """
    import hashlib
    import os
    import urllib.parse
    import urllib.request

    cache_dir = Path(
        os.environ.get("GSFLUENT_MODEL_CACHE_DIR", str(_DEFAULT_MODEL_CACHE))
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(model_path_on_server.encode()).hexdigest()[:16]
    local_path = cache_dir / f"{key}.ply"
    if local_path.exists():
        return local_path

    url = f"{server_base.rstrip('/')}/api/models/file?" \
          f"path={urllib.parse.quote(model_path_on_server)}"
    tmp = local_path.with_suffix(".ply.partial")
    with urllib.request.urlopen(url, timeout=120) as r:
        tmp.write_bytes(r.read())
    tmp.rename(local_path)
    return local_path


def mmap_cell(npz_path: Path) -> dict:
    """Mmap a sequence .npz and pre-compute its bbox.

    Handles both schemas:
      v1: has `cov` (n,3,3) — static covariance, splats smear during motion
      v2: has `quats` (n_frames,n,4) + `scales` (n,3) — per-frame covariance
          reconstructed in the push loop, ellipsoids rotate with the
          deformation (sharp during motion). Set `version` field to 2.

    bbox is from frame 0 — for animated sequences the first frame is the
    rest pose, which is what we want the grid + initial camera framed to.
    Worst-case frames (splashes, flying debris) would over-zoom-out the
    initial view if we sized to the union.

    Scaling: bbox and cov are multiplied by _VISER_K / _VISER_K^2
    respectively so cov stays in float16's normal range when viser
    casts for WS transport. Frames are mmap'd and multiplied lazily
    at frame-access time to preserve the OS page-on-demand savings."""
    d = np.load(npz_path, mmap_mode="r")
    f0 = np.asarray(d["frames"][0]) * _VISER_K
    bbox_lo = f0.min(axis=0).astype(np.float32)
    bbox_hi = f0.max(axis=0).astype(np.float32)
    # Floor reference for the grid mesh — we want the grid to sit at
    # or below the lowest splat across the WHOLE sequence, not just
    # frame 0, so when the animation drops splats lower than the rest
    # pose (collapse, earthquake displacement, jelly stretch) they
    # don't sink through the visible ground. Cheap: scan all frame
    # z-coords once at load. Only z is needed.
    frames_z = np.asarray(d["frames"][..., 2])
    floor_z = float(frames_z.min() * _VISER_K)
    bbox_lo[2] = floor_z
    K2 = _VISER_K * _VISER_K

    if "quats" in d.files and "scales" in d.files:
        # v2: precompute K-scaled scales² so push-loop cov reconstruction
        # is one multiply + one matmul per splat. ~1 ms for 683k splats
        # with numpy's vectorized einsum on the push thread.
        scales = np.asarray(d["scales"]).astype(np.float32)   # (n, 3)
        return {
            "version": 2,
            "frames": d["frames"],                            # mmap, raw
            "quats": d["quats"],                              # mmap, raw
            "scales_sq": (scales * scales) * K2,              # K² for cov
            "rgb": d["rgb"],
            "opacity": d["opacity"],
            "bbox_lo": bbox_lo,
            "bbox_hi": bbox_hi,
        }
    else:
        # v1: pre-multiply cov by K². ~24 MB allocation for 683k splats —
        # cheap, only done once per cell load.
        return {
            "version": 1,
            "frames": d["frames"],                            # mmap, raw
            "cov": np.asarray(d["cov"]).astype(np.float32) * K2,
            "rgb": d["rgb"],
            "opacity": d["opacity"],
            "bbox_lo": bbox_lo,
            "bbox_hi": bbox_hi,
        }


def mmap_model_cell(ply_path: Path) -> dict:
    """Parse a single-frame model cell from a 3DGS .ply file.

    Mirrors mmap_cell's output shape so the rest of the render loop
    treats models the same way as 1-frame sequences. Unlike mmap_cell
    we *don't* mmap — plyfile materializes the arrays. Models are
    small enough (one frame, ≤200 MB) that the page-on-demand
    optimization doesn't earn its keep here.

    Drops the higher-order SH coefficients (f_rest_*) — viser's splat
    primitive only consumes positions + cov + rgb + opacity. The full
    SH would be wasted bytes.

    Mathematical conversions (3DGS .ply → viser numpy):
      - scales:  exp(scale_*)
      - opacity: sigmoid(opacity_raw)
      - rgb:     clip(0.5 + 0.282 * f_dc_*, 0, 1)  [zero-order SH]
      - quats:   normalize((rot_0, rot_1, rot_2, rot_3))
    """
    from plyfile import PlyData
    v = PlyData.read(str(ply_path)).elements[0]

    xyz = np.stack([v["x"], v["y"], v["z"]], axis=-1).astype(np.float32)
    # viser's splat primitive requires opacity shape (N, 1), not (N,)
    # — same convention sequence_to_viser_npz.py writes to .npz.
    opacity = 1.0 / (1.0 + np.exp(-np.asarray(v["opacity"]).astype(np.float32)))
    opacity = opacity.reshape(-1, 1)
    scales = np.exp(np.stack(
        [v["scale_0"], v["scale_1"], v["scale_2"]], axis=-1,
    )).astype(np.float32)
    # fp16 cov floor: viser transports cov as fp16 over the websocket.
    # Splats with any scale axis below sqrt(6.1e-5) ≈ 7.81e-3 produce
    # cov-diagonal entries below fp16's normal floor → silently flushed
    # to zero or culled at the renderer. ~68% of splats in a typical
    # 3DGS scan hit this. Without the clamp, only the anisotropic
    # outliers (whose covariance survives fp16) render — visually a
    # field of vertical streaks instead of proper Gaussian blobs.
    # 7.81e-3 world units is sub-pixel on any practical scene. Mirrors
    # the clamp server/tools/sequence_to_viser_npz.py applies for sim outputs.
    _FP16_COV_FLOOR_SQRT = np.float32(np.sqrt(6.1e-5))
    np.maximum(scales, _FP16_COV_FLOOR_SQRT, out=scales)
    quats_raw = np.stack(
        [v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=-1,
    ).astype(np.float32)
    quats = quats_raw / (np.linalg.norm(quats_raw, axis=-1, keepdims=True) + 1e-9)
    SH_C0 = 0.28209479177387814
    f_dc = np.stack(
        [v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=-1,
    ).astype(np.float32)
    rgb = np.clip(0.5 + SH_C0 * f_dc, 0.0, 1.0)

    # Recenter to origin. 3DGS models often live at large world coords
    # (e.g. UTM-derived [3460, 29045]); rendered without recentering, the
    # view-matrix subtraction in WebGL drops fp32 precision from ~3 mm
    # absolute → ~0.5 m in eye-space (catastrophic cancellation at
    # magnitude 30k). Splats jitter, Z-fight, or get culled — the viewer
    # ends up empty. Sequences are already authored in a local frame, so
    # this is a model-only correction. We don't return the offset because
    # everything downstream (camera framing, grid, gizmo) reads from the
    # cell's bbox, which is now also centered.
    bbox_center = ((xyz.min(axis=0) + xyz.max(axis=0)) * 0.5).astype(np.float32)
    xyz_local = (xyz - bbox_center).astype(np.float32)

    f0 = xyz_local * _VISER_K
    bbox_lo = f0.min(axis=0).astype(np.float32)
    bbox_hi = f0.max(axis=0).astype(np.float32)
    K2 = _VISER_K * _VISER_K

    return {
        "version": 2,
        "frames": xyz_local[None, :, :],
        "quats": quats[None, :, :],
        "scales_sq": (scales * scales) * K2,
        "rgb": rgb,
        "opacity": opacity,
        "bbox_lo": bbox_lo,
        "bbox_hi": bbox_hi,
    }


def _quats_to_R(quats: np.ndarray) -> np.ndarray:
    """Batched quaternion (N,4 with w,x,y,z) → (N,3,3) rotation matrices.

    Inputs are expected unit-normalized (sequence_to_viser_npz.py
    normalizes when writing v2). Matches the math in
    `frontend/python/sequence_to_viser_npz.py:_quat_to_R` so v2 cov reconstruction
    is bit-identical to the v1 static cov when applied to frame 0."""
    qw = quats[:, 0]; qx = quats[:, 1]; qy = quats[:, 2]; qz = quats[:, 3]
    n = qw.shape[0]
    R = np.empty((n, 3, 3), dtype=np.float32)
    R[:, 0, 0] = 1 - 2 * (qy * qy + qz * qz)
    R[:, 0, 1] = 2 * (qx * qy - qz * qw)
    R[:, 0, 2] = 2 * (qx * qz + qy * qw)
    R[:, 1, 0] = 2 * (qx * qy + qz * qw)
    R[:, 1, 1] = 1 - 2 * (qx * qx + qz * qz)
    R[:, 1, 2] = 2 * (qy * qz - qx * qw)
    R[:, 2, 0] = 2 * (qx * qz - qy * qw)
    R[:, 2, 1] = 2 * (qy * qz + qx * qw)
    R[:, 2, 2] = 1 - 2 * (qx * qx + qy * qy)
    return R


def _cov_for_frame(data: dict, frame_idx: int) -> np.ndarray:
    """Per-frame Σᵢ = Rᵢ · diag(scales²) · Rᵢᵀ for v2, or just the static
    cov for v1. Returns a (n, 3, 3) float32 array suitable for assignment
    to viser's `splat.covariances`."""
    if data["version"] == 1:
        return np.ascontiguousarray(data["cov"])
    q = np.asarray(data["quats"][frame_idx])               # (n, 4)
    R = _quats_to_R(q)                                     # (n, 3, 3)
    S2 = data["scales_sq"]                                 # (n, 3)
    # Scale each column of R by S² (because Σ = R · diag(s²) · Rᵀ ⇒
    # R · diag(s²) writes the diagonal as a per-column multiplier).
    # The (n, 1, 3) broadcast over the last axis is the right shape.
    R_S2 = R * S2[:, None, :]                              # (n, 3, 3)
    cov = np.einsum("nij,nkj->nik", R_S2, R).astype(np.float32)
    return np.ascontiguousarray(cov)


def _grid_params_for_bbox(lo: np.ndarray, hi: np.ndarray) -> dict:
    """Match the React Viewport grid sizing formula.

    React Viewport uses:
        cellSize    = max(sceneScale / 50, 0.001)
        sectionSize = max(sceneScale / 5,  0.01)
    where sceneScale = max(bbox.extent). We pick the same divisions so
    Points-mode ↔ Splat-mode toggle doesn't snap to a different grid
    cadence.

    The grid extent (width/height in viser's add_grid) is set generously
    — ~8× the model's largest axis — so the camera never sees the edge."""
    extent = np.maximum(hi - lo, 1e-6).astype(np.float32)
    scene_scale = float(extent.max())
    return {
        "cell_size": max(scene_scale / 50.0, 0.001),
        "section_size": max(scene_scale / 5.0, 0.01),
        "plane_size": max(scene_scale * 8.0, 8.0),
        "scene_scale": scene_scale,
    }


def _camera_for_bbox(lo: np.ndarray, hi: np.ndarray) -> tuple[tuple[float, float, float],
                                                              tuple[float, float, float]]:
    """Frame a camera that comfortably contains the bbox.

    Look-at = bbox center. Position = bbox center + (diag, diag, diag×0.7)
    where diag = ‖bbox.size‖₂ — same formula SplatScene's auto-fit uses
    on the R3F side (THREE.Vector3.length() of the bbox extents). Using
    the diagonal instead of max-extent × 0.8 makes the two modes frame
    the model at identical distances, so toggling Points ↔ Splat doesn't
    visibly jump the camera. The +Z component is smaller than +X/+Y so
    the camera looks slightly *down* on the model — most fluid /
    destruction scenes read better from above-eye level."""
    center = ((lo + hi) * 0.5).astype(float)
    extent = np.maximum(hi - lo, 1e-6).astype(float)
    diag = float(np.linalg.norm(extent))
    offset = np.array([diag, diag, diag * 0.7], dtype=float)
    position = tuple((center + offset).tolist())
    look_at = tuple(center.tolist())
    return position, look_at  # type: ignore[return-value]


def _near_for_distance(dist: float, scene_scale: float) -> float:
    """Near plane that tracks the camera-to-target distance — the same
    formula SplatScene uses on the R3F side. Floor at scene_scale * 1e-6
    (very tight) so even when the user orbits to the *rear* of the model
    and the rear surface ends up at view-z < initial_near, the splats
    there still render. far/near ratio stays inside the 24-bit fp depth
    buffer's ~16M useful steps because we don't blow up `far` to match.

    Without this adaptive near, viser's runtime default (~0.1 from the
    library) culls the surface closest to the camera whenever it dips
    inside the near plane — which is what 'rear surface vanishes when
    I orbit behind the model' reports."""
    return max(dist * 0.0005, scene_scale * 1e-6)


def _camera_far_for_scene(scene_scale: float) -> float:
    """Far plane sized to feel effectively infinite. The 3DGS splat
    rasterizer in viser uses GPU-side depth sort for alpha blending —
    far/near ratio in the 1e6-1e7 range stays well inside fp32 depth
    precision, so we can be generous. Old `scene_scale * 100` was
    visibly close on big scenes; this bumps to scene_scale * 10000 with
    a 1e6 absolute floor for tiny scenes."""
    return max(scene_scale * 10000.0, 1.0e6)


class SetBody(BaseModel):
    cell: str | None = None
    frame: int | None = None


class CameraBody(BaseModel):
    # Either position+target (workbench-style) or position+wxyz (viser-native).
    # Workbench sends position+target; we convert to look_at directly.
    position: tuple[float, float, float] | None = None
    target:   tuple[float, float, float] | None = None
    wxyz:     tuple[float, float, float, float] | None = None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--npz_dir", required=True,
                   help="Directory containing per-sequence .npz files")
    p.add_argument("--viser_port", type=int, default=8091,
                   help="Port for viser's HTTP+WS (where the iframe points)")
    p.add_argument("--control_port", type=int, default=8092,
                   help="Port for the headless control API (where React POSTs)")
    p.add_argument("--server", default="http://localhost:8080",
                   help="Backend base URL (where /api/models/file lives). "
                        "Default: http://localhost:8080 (the SSH tunnel "
                        "target run-client.sh sets up).")
    p.add_argument("--sync_status_file", type=Path, default=None,
                   help="Path to frontend/python/sync_daemon.py's status JSON. Default: "
                        "$XDG_RUNTIME_DIR/gsfluent_sync_status.json (matches the "
                        "daemon's own default). Surfaced verbatim through "
                        "GET /sync-status for the workbench's diagnostics pill.")
    p.add_argument("--bind", default="127.0.0.1",
                   help="Bind address for both viser WS (--viser_port) and "
                        "the control API (--control_port). Default: 127.0.0.1 "
                        "(loopback only, correct for the local-rendering "
                        "deployment where viser and the browser run on the "
                        "same machine). Use 0.0.0.0 only if you intentionally "
                        "want other hosts on the network to reach this viser "
                        "process — e.g. server-side deployment where the "
                        "browser is remote. Be aware of the security "
                        "implication: viser has no auth; anyone reachable on "
                        "the bound port can read/manipulate the scene.")
    args = p.parse_args()

    # Resolve sync-daemon status path the same way the daemon does, so
    # the default config "just works" without a CLI flag on either side.
    if args.sync_status_file is None:
        import os as _os
        _xdg = _os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{_os.getuid()}"
        args.sync_status_file = Path(_xdg) / "gsfluent_sync_status.json"

    npz_root = Path(args.npz_dir)
    npz_paths = sorted(npz_root.glob("*.npz"))
    if not npz_paths:
        print(f"ERROR: no .npz in {npz_root}")
        return 2

    print(f"mmap-loading {len(npz_paths)} cells from {npz_root}...")
    cells: dict[str, dict] = {}
    for path in npz_paths:
        # Key cells by their wire-format name (`sequence:<stem>`) so they
        # match what the React workbench sends. Without this, pre-mmap'd
        # cells live under bare names while lazy-loaded ones live under
        # the prefixed form — the SPA then flags the bare-name pre-mmap
        # cells as "not in viser cache" because it only knows the wire
        # form. Models go through resolve_cell_lazily on demand and
        # already land under `model:<name>`, so we don't add any here.
        key = f"sequence:{path.stem}"
        try:
            cells[key] = mmap_cell(path)
        except (KeyError, ValueError, OSError) as e:
            # A single malformed .npz (missing cov / quats / scales /
            # truncated archive) shouldn't kill the whole renderer.
            # Skip it with a warning so the remaining cells still load.
            print(f"  {key}: SKIPPED — {type(e).__name__}: {e}")
            continue
        c = cells[key]
        print(f"  {key}: {c['frames'].shape}  "
              f"bbox=({c['bbox_lo']}, {c['bbox_hi']})")
    if not cells:
        print(f"ERROR: every .npz in {npz_root} was malformed")
        return 2

    def _set_loading(name: str | None, phase: str | None, error: str | None = None) -> None:
        """Brief-locked update to state["loading"] so concurrent /state polls
        can read in-flight progress. Pass (None, None) to clear."""
        with lock:
            if name is None:
                state["loading"] = None
            else:
                state["loading"] = {"name": name, "phase": phase, "error": error}

    def resolve_cell_lazily(name: str) -> tuple[bool, str | None]:
        """If `name` is not yet a loaded cell, try to load it.

        Resolution order:
          1. model:<modelName>  → fetch via /api/models, then .ply, then mmap_model_cell
          2. sequence:<seqName> → look for <seqName>.npz under npz_root
          3. bare <name>        → try sequence first, then model (transition fallback)

        Returns (ok, error). `error` is a short tag from the set:
          - "not_found"    backend doesn't know this model
          - "fetch_failed" network / HTTP error fetching the .ply
          - "parse_failed" ply or npz parse failed
          - "io_failed"    other I/O error
        Updates `cells` in place. Idempotent — a re-call with an
        already-loaded name is a no-op. Posts intermediate phases via
        _set_loading so the SPA can show progress.
        """
        import urllib.request, json as _json
        if name in cells:
            return True, None

        def _try_model(model_name: str) -> tuple[bool, str | None]:
            _set_loading(name, "fetching")
            try:
                with urllib.request.urlopen(
                    f"{args.server.rstrip('/')}/api/models",
                    timeout=10,
                ) as r:
                    listing = _json.loads(r.read())
            except Exception as e:
                print(f"  resolve {name}: failed to list models: {e}")
                return False, "fetch_failed"
            entry = next((m for m in listing if m["name"] == model_name), None)
            if entry is None:
                return False, "not_found"
            try:
                local_ply = fetch_model_ply(args.server, entry["path"])
            except Exception as e:
                print(f"  resolve {name}: model fetch failed: {e}")
                return False, "fetch_failed"
            _set_loading(name, "parsing")
            try:
                cells[name] = mmap_model_cell(local_ply)
                print(f"  loaded model cell {name} (from {local_ply})")
                return True, None
            except Exception as e:
                print(f"  resolve {name}: ply parse failed: {e}")
                return False, "parse_failed"

        def _try_sequence(seq_name: str) -> tuple[bool, str | None]:
            p = npz_root / f"{seq_name}.npz"
            if not p.is_file():
                return False, "not_found"
            _set_loading(name, "parsing")
            try:
                cells[name] = mmap_cell(p)
                print(f"  loaded sequence cell {name} from {p}")
                return True, None
            except Exception as e:
                print(f"  resolve {name}: npz mmap failed: {e}")
                return False, "parse_failed"

        if name.startswith("model:"):
            return _try_model(name[len("model:"):])
        if name.startswith("sequence:"):
            return _try_sequence(name[len("sequence:"):])
        ok, err = _try_sequence(name)
        if ok:
            return ok, err
        return _try_model(name)

    # --- viser scene -----------------------------------------------------
    server = viser.ViserServer(host=args.bind, port=args.viser_port)

    # Theme: match the workbench dark scheme so the iframe doesn't read as
    # a foreign element. Hiding logo + share button is what removes most
    # of viser's branding chrome; control_layout='floating' keeps any GUI
    # (we add none) out of a sticky sidebar.
    server.gui.configure_theme(
        dark_mode=True,
        show_logo=False,
        show_share_button=False,
        control_layout="floating",
        brand_color=_ACCENT_RGB,
    )
    # Hide the right-side panel label entirely — there's nothing in it.
    server.gui.set_panel_label(None)

    # Force the GL clear color to match tailwind `canvas` (#0a0f1a) so
    # toggling Points ↔ Splat in the React workbench doesn't flash a
    # different background. Viser doesn't expose a direct clear-color
    # API — `set_background_image` is the supported hook; a uniform
    # 16×16 tile gets stretched over the viewport and behaves like a
    # solid clear color. The image is stamped once at startup; viser
    # composites it behind every frame.
    _clear_tile = np.full((16, 16, 3), _CANVAS_RGB, dtype=np.uint8)
    server.scene.set_background_image(_clear_tile)
    # World axes overlay (the big +X/+Y/+Z triad at world origin) — off;
    # we add a smaller frame at the scene's floor corner so the iframe
    # still carries an orientation cue without dominating the view.
    server.scene.world_axes.visible = False

    # Bootstrap scene helpers (grid, gizmo, initial camera) using the
    # first cell's bbox as a reasonable default — but DO NOT auto-load
    # the cell's splat. The frontend should show an empty scene until
    # the user explicitly picks something in the outliner.
    cur_name = next(iter(cells))
    cur = cells[cur_name]
    splat = None

    # Adaptive grid + small floor-corner gizmo, both per-cell — when a
    # different sequence is loaded the grid + gizmo reposition with the
    # new bbox. Storing the handles lets us mutate them in place.
    grid_params = _grid_params_for_bbox(cur["bbox_lo"], cur["bbox_hi"])
    bbox_center = ((cur["bbox_lo"] + cur["bbox_hi"]) * 0.5).astype(float)
    # Ground sits at world z=0 — the convention the pitch view uses for
    # "where the building rests." Splats and camera target both lift by
    # `floor_lift` so the cell's lowest point coincides with z=0 instead
    # of the original sim-coord bbox_lo[2] (which is usually negative).
    floor_lift = -float(cur["bbox_lo"][2])
    floor_z = 0.0
    grid = server.scene.add_grid(
        "ground",
        width=grid_params["plane_size"],
        height=grid_params["plane_size"],
        plane="xy",
        cell_size=grid_params["cell_size"],
        cell_color=_GRID_CELL_RGB,
        section_size=grid_params["section_size"],
        section_color=_ACCENT_RGB,
        position=(float(bbox_center[0]), float(bbox_center[1]), floor_z),
    )
    # Floor-corner gizmo: smaller, at the bbox's (xmin, ymin, zmin) corner,
    # sized relative to scene scale so it's never the dominant visual.
    gizmo_size = grid_params["scene_scale"] * 0.05
    gizmo = server.scene.add_frame(
        "gizmo",
        show_axes=False,
        axes_length=gizmo_size,
        axes_radius=gizmo_size * 0.04,
        position=(float(cur["bbox_lo"][0]),
                  float(cur["bbox_lo"][1]),
                  floor_z),
    )

    # Initial camera: frame the active cell. Applies to clients connecting
    # AFTER this is set; for clients connected at startup we re-apply on
    # the first /set or on the on_client_connect hook below.
    pos0, look0 = _camera_for_bbox(cur["bbox_lo"], cur["bbox_hi"])
    # Camera was framed against the un-lifted bbox; the splat node is
    # rendered with a (0,0,floor_lift) offset, so the camera target +
    # position both need the same upward shift to keep the model in view.
    pos0  = (pos0[0],  pos0[1],  pos0[2]  + floor_lift)
    look0 = (look0[0], look0[1], look0[2] + floor_lift)
    cur_scale = grid_params["scene_scale"]
    cam_dist0 = float(np.linalg.norm(np.asarray(pos0) - np.asarray(look0)))
    server.initial_camera.position = pos0
    server.initial_camera.look_at = look0
    server.initial_camera.up = (0.0, 0.0, 1.0)
    server.initial_camera.fov = float(np.deg2rad(50.0))   # match React's fov=50
    server.initial_camera.near = _near_for_distance(cam_dist0, cur_scale)
    server.initial_camera.far  = _camera_far_for_scene(cur_scale)

    # Shared state between control API and the render thread.
    # `cell` starts as None: the frontend will see /state.cell=null
    # until the user picks an outliner item. Otherwise viser would
    # auto-start playing the first mmap'd cell, which surprises users.
    state = {
        "cell": None,
        "frame": 0,
        "pushed_cell": None,
        "pushed_frame": -1,
        # Cached last-known camera so the React side can read it via
        # GET /camera without having to subscribe to viser's own WS.
        # Updated by the on-update callback below.
        "camera": {
            "position": list(pos0),
            "target":   list(look0),
            "wxyz":     [1.0, 0.0, 0.0, 0.0],
        },
        # If True, the next render-loop tick should also push grid/gizmo
        # repositioning + a fresh initial_camera. Set on cell-swap.
        "scene_dirty": True,
        # In-flight lazy-resolution progress. None when idle, else a
        # {"name": "model:foo", "phase": "fetching|parsing", "error": null}
        # dict. The SPA polls /state every 500ms and surfaces this as a
        # status overlay so loads of large 3DGS models don't look frozen.
        "loading": None,
    }
    lock = threading.Lock()

    def _rebuild_scene_node():
        """Remove + re-add the splat node for the current cell.
        Called on cell-swap. ~10ms on cluster_6_15-class data."""
        nonlocal splat
        cur_c = cells[state["cell"]]
        centers = np.ascontiguousarray(
            np.asarray(cur_c["frames"][state["frame"]]) * _VISER_K
        )
        if splat is not None:
            try:
                splat.remove()
            except Exception:
                pass
            splat = None
        splat = server.scene.add_gaussian_splats(
            "splat",
            centers=centers,
            covariances=_cov_for_frame(cur_c, state["frame"]),
            rgbs=np.ascontiguousarray(cur_c["rgb"]),
            opacities=np.ascontiguousarray(cur_c["opacity"]),
            position=(0.0, 0.0, -float(cur_c["bbox_lo"][2])),
        )
        # The rebuild just populated the node with the current frame's
        # data, so the render loop doesn't need to push again this tick.
        state["pushed_cell"] = state["cell"]
        state["pushed_frame"] = state["frame"]

    # Skip startup rebuild — `state["cell"]` is None until the user
    # explicitly picks a cell via /set. _rebuild_scene_node assumes a
    # non-None cell, so calling it here would index cells[None] → crash.

    # When a client connects: re-apply initial camera + register an
    # on_update so user-driven orbits get reflected back into our
    # cached state for GET /camera reads.
    @server.on_client_connect
    def _on_connect(client: viser.ClientHandle) -> None:
        # Set the camera position/look_at the user was previously at.
        # initial_camera.near/far are already configured globally above —
        # not setting them per-client because writing client.camera.near
        # mid-handshake caused viser to silently close the WS in our
        # local 1.0.20 build. Static initial near is good enough for the
        # 0.6m models we use today; revisit if zoom-in clipping returns
        # on bigger scenes.
        with lock:
            pos = tuple(state["camera"]["position"])
            tgt = tuple(state["camera"]["target"])
        try:
            client.camera.position = pos
            client.camera.look_at = tgt
            client.camera.up_direction = (0.0, 0.0, 1.0)
        except Exception as e:
            print(f"[viser_headless] on_connect tune failed: {e}")

        @client.camera.on_update
        def _on_cam(cam: viser.CameraHandle) -> None:
            # Cache position/look_at/wxyz for the GET endpoint. No echo:
            # we never push these back into client.camera.* — that would
            # fight the user's input. Errors get swallowed so a write
            # quirk in viser's WS path can't kill the renderer.
            try:
                with lock:
                    state["camera"] = {
                        "position": [float(x) for x in cam.position],
                        "target":   [float(x) for x in cam.look_at],
                        "wxyz":     [float(x) for x in cam.wxyz],
                    }
            except Exception as e:
                print(f"[viser_headless] on_update failed: {e}")

    # --- control API (sidecar FastAPI) -----------------------------------
    api = FastAPI()
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @api.post("/set")
    def set_state(body: SetBody) -> dict:
        # Slow-path: cell needs lazy resolution. Don't hold the lock
        # during the network fetch + mmap or every /state poll blocks
        # for the duration of the load (30-90s for a real .ply). We
        # set state["loading"] before/during so SPA polls show
        # phase progress, then re-acquire briefly to commit the cell.
        if body.cell is not None and body.cell not in cells:
            ok, err = resolve_cell_lazily(body.cell)
            if not ok:
                _set_loading(body.cell, "error", err)
                return {"ok": False, "error": err or "unknown_cell",
                        "cell": body.cell, "cells": list(cells)}
            _set_loading(None, None)

        with lock:
            if body.cell is not None:
                # By the time we reach here, the cell is guaranteed to
                # be in `cells` (or it's a no-op when already-active).
                if body.cell != state["cell"]:
                    state["cell"] = body.cell
                    state["scene_dirty"] = True   # grid + camera resize next tick
                    # Cells can have different frame counts (e.g. 60 vs 150).
                    # If the new cell is shorter than the old frame index,
                    # the render loop's `data["frames"][frame]` would raise
                    # IndexError and silently kill the render thread. Clamp.
                    n_new = cells[state["cell"]]["frames"].shape[0]
                    if state["frame"] >= n_new:
                        state["frame"] = max(0, n_new - 1)
                    _rebuild_scene_node()
            if body.frame is not None:
                # Frame-only updates are valid even with no cell selected
                # — the React workbench fires a /set on every store
                # change, including the first mount when activeCell is
                # still null. Without this guard, indexing cells[None]
                # raises KeyError and the response is a noisy 500.
                cur = state["cell"]
                if cur is not None and cur in cells:
                    n = cells[cur]["frames"].shape[0]
                    state["frame"] = max(0, min(int(body.frame), n - 1))
                else:
                    state["frame"] = max(0, int(body.frame))
            return {"ok": True, "cell": state["cell"], "frame": state["frame"]}

    @api.post("/clear")
    def clear_state() -> dict:
        """Drop the active scene node so the viewport is empty on next render.

        viser_headless persists scene state across SPA reloads, which is
        the right default during a sim session (camera + splat survive
        F5). But when the React app's activeCell is null (no model, no
        sequence picked) and the user opens the SPA fresh, viser will
        replay the old splat to the new client — the workbench shows
        "no model loaded" while the iframe still paints a building.

        POST /clear from the React side breaks that tie: when wireName
        flips to null the SPA fires this and the next client sees an
        empty scene.
        """
        nonlocal splat
        with lock:
            if splat is not None:
                try:
                    splat.remove()
                except Exception:  # noqa: BLE001
                    pass
                splat = None
            state["cell"] = None
            state["frame"] = 0
            state["scene_dirty"] = True
            return {"ok": True}

    @api.get("/state")
    def get_state() -> dict:
        with lock:
            cell = state["cell"]
            loading = state.get("loading")
            # cell may be None at startup (no auto-load). The frontend
            # treats null as "viewport empty, waiting for outliner pick".
            if cell is None or cell not in cells:
                return {
                    "cell": None,
                    "frame": 0,
                    "n_frames": 0,
                    "cells": list(cells),
                    "bbox": None,
                    "loading": loading,
                }
            cur_c = cells[cell]
            return {
                "cell": cell,
                "frame": state["frame"],
                "n_frames": cur_c["frames"].shape[0],
                "cells": list(cells),
                "loading": loading,
                "bbox": {
                    "lo": cur_c["bbox_lo"].tolist(),
                    "hi": cur_c["bbox_hi"].tolist(),
                },
            }

    @api.get("/sync-status")
    def sync_status() -> dict:
        """Return frontend/python/sync_daemon.py's most recent status snapshot.

        The daemon writes this JSON every tick. We pass it through verbatim
        so the workbench's diagnostics pill can render "online?" + last sync
        timestamp + per-sequence mirror state without having to know where
        the file lives on disk. Missing file = daemon not running yet.
        """
        try:
            import json as _json
            return _json.loads(args.sync_status_file.read_text())
        except FileNotFoundError:
            return {"online": False, "error": "no status file yet "
                                              "(is sync_daemon running?)"}
        except (OSError, ValueError) as e:
            return {"online": False, "error": f"status file unreadable: {e}"}

    @api.get("/read-local")
    def read_local(path: str):
        """Stream a .ply file from the client's filesystem to the
        workbench. The SPA can't read local files except via the
        drag-drop FileReader path; this endpoint lets a user instead
        paste a filesystem path and have the workbench load it.

        The SPA then re-uploads the bytes via /api/models/upload, so
        the server (which never sees the client's filesystem) ends up
        with a normal model registration. We're only the file-reader
        leg of the trip.

        Security: only .ply files, and only files actually on disk —
        path traversal is harmless because we always resolve and then
        stat the result. A malicious caller could enumerate which .ply
        paths exist on the client, but viser_headless binds 0.0.0.0
        already (same threat surface), and the response is just bytes
        of files the user owns.
        """
        from fastapi.responses import FileResponse
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise HTTPException(404, f"no such file: {p}")
        if p.suffix.lower() != ".ply":
            raise HTTPException(400, f"only .ply files accepted, got {p.suffix}")
        return FileResponse(
            str(p),
            media_type="application/octet-stream",
            filename=p.name,
        )

    @api.post("/sync_cell")
    def sync_cell(name: str, url: str) -> dict:
        """Download an .npz from `url` and mmap it as cell `name`.

        Used by the SPA's on-demand cache flow: when a sequence has no
        local .npz, the SPA POSTs /api/sequences/{name}/cache/build to
        the backend to build it server-side, then calls this endpoint
        with the download URL so the client picks up the artifact and
        registers the cell. Removes the need for sync_daemon for the
        single-cell case.

        Path-traversal defense same as /reload: `name` must match
        _SAFE_NAME and the resolved file must stay under npz_dir.
        """
        if not _SAFE_NAME.match(name):
            return {"ok": False, "error": f"invalid cell name: {name!r}"}
        npz_path = (npz_root / f"{name}.npz").resolve()
        try:
            npz_path.relative_to(npz_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes npz_dir: {name!r}"}

        # Stream-download to a `.partial` sibling then atomic-rename, so
        # an interrupted download doesn't leave a corrupt .npz that
        # mmap_cell would later choke on.
        partial = npz_path.with_suffix(".npz.partial")
        try:
            with httpx.stream("GET", url, timeout=600.0,
                              follow_redirects=True) as r:
                if r.status_code != 200:
                    return {"ok": False, "error":
                            f"download failed: HTTP {r.status_code}"}
                with open(partial, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
            partial.replace(npz_path)
        except Exception as e:
            partial.unlink(missing_ok=True)
            return {"ok": False, "error": f"download failed: {e}"}

        try:
            new_data = mmap_cell(npz_path)
        except Exception as e:
            return {"ok": False, "error": f"mmap failed: {e}"}
        with lock:
            was_new = name not in cells
            cells[name] = new_data
            if state["cell"] == name:
                state["scene_dirty"] = True
                state["pushed_frame"] = -1
        return {"ok": True, "cell": name, "added": was_new,
                "bytes": npz_path.stat().st_size,
                "n_frames": new_data["frames"].shape[0]}

    @api.post("/reload")
    def reload_cell(cell: str | None = None) -> dict:
        """Re-mmap a cell's .npz from disk.

        Called by frontend/python/sync_daemon.py after it downloads a fresh copy.
        If `cell` matches a currently-loaded one, the new mmap replaces
        the old; if it's a previously-unknown name, the cell is added
        and becomes available via POST /set.

        When the reloaded cell is the active one, scene_dirty flips so
        the next render tick re-pushes grid/camera + frame to viser.
        """
        if cell is None:
            return {"ok": False, "error": "missing ?cell=<name>"}
        # Path-traversal defense: `cell` becomes part of a filesystem
        # path. Reject anything that's not a plain library name. Also
        # belt-and-suspenders with a resolved-path containment check
        # in case the regex misses something exotic.
        if not _SAFE_NAME.match(cell):
            return {"ok": False, "error": f"invalid cell name: {cell!r}"}
        npz_path = (npz_root / f"{cell}.npz").resolve()
        try:
            npz_path.relative_to(npz_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes npz_dir: {cell!r}"}
        if not npz_path.is_file():
            return {"ok": False, "error": f"no .npz at {npz_path}"}
        try:
            new_data = mmap_cell(npz_path)
        except Exception as e:
            return {"ok": False, "error": f"mmap failed: {e}"}
        with lock:
            was_new = cell not in cells
            cells[cell] = new_data
            if state["cell"] == cell:
                # Force a full re-push: cells[cell] is a fresh dict, so
                # the static attrs (cov/rgb/opacity) and the bbox-derived
                # grid + camera all need to be re-asserted.
                state["scene_dirty"] = True
                state["pushed_frame"] = -1
        return {"ok": True, "cell": cell, "added": was_new,
                "n_frames": new_data["frames"].shape[0]}

    @api.get("/camera")
    def get_camera() -> dict:
        with lock:
            return dict(state["camera"])

    @api.get("/clients_debug")
    def clients_debug() -> dict:
        """Inspect what near/far/fov each connected client is actually
        running with. Used to confirm whether server.initial_camera.near
        actually propagates to the runtime client camera, or whether
        viser overrides it internally on orbit."""
        clients_out = []
        for cid, c in server.get_clients().items():
            try:
                clients_out.append({
                    "id":   cid,
                    "near": float(c.camera.near),
                    "far":  float(c.camera.far),
                    "fov":  float(c.camera.fov),
                    "pos":  [float(x) for x in c.camera.position],
                    "look_at": [float(x) for x in c.camera.look_at],
                    "dist": float(np.linalg.norm(
                        np.asarray(c.camera.position) -
                        np.asarray(c.camera.look_at))),
                })
            except Exception as e:
                clients_out.append({"id": cid, "error": str(e)})
        return {
            "initial_camera": {
                "near": float(server.initial_camera.near),
                "far":  float(server.initial_camera.far),
            },
            "clients": clients_out,
        }

    @api.post("/camera")
    def set_camera(body: CameraBody) -> dict:
        # Push a (position, target) or (position, wxyz) into every
        # connected client. Workbench typically sends position+target;
        # falling back to wxyz lets us also re-hydrate from a viser-native
        # camera state if we ever need it.
        clients = server.get_clients()
        for c in clients.values():
            if body.position is not None:
                c.camera.position = body.position
            if body.target is not None:
                c.camera.look_at = body.target
            elif body.wxyz is not None:
                c.camera.wxyz = body.wxyz
        # Cache the requested state so an immediate GET /camera reflects
        # it even if no client has echoed an on_update yet.
        with lock:
            if body.position is not None:
                state["camera"]["position"] = list(body.position)
            if body.target is not None:
                state["camera"]["target"] = list(body.target)
            if body.wxyz is not None:
                state["camera"]["wxyz"] = list(body.wxyz)
        return {"ok": True, "clients": len(clients)}

    def run_api():
        uvicorn.run(
            api,
            host=args.bind,
            port=args.control_port,
            log_level="warning",
        )

    threading.Thread(target=run_api, daemon=True).start()

    # SIGINT/SIGTERM → set a flag the render loop checks on the next
    # tick, so we exit cleanly (stopping viser's WS server + the
    # uvicorn thread via daemon-thread tearing-down) instead of dying
    # mid-WS-send. Without this, ctrl-C kills the process while the WS
    # transport is mid-frame, occasionally leaving the browser tab in
    # a half-connected zombie state.
    stop_flag = {"v": False}
    def _stop(*_):
        stop_flag["v"] = True
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    print(f"\n>>> viser viewport: http://localhost:{args.viser_port}")
    print(f">>> control API:    http://localhost:{args.control_port}/set\n")

    # --- render loop -----------------------------------------------------
    while not stop_flag["v"]:
        with lock:
            cell = state["cell"]
            frame = state["frame"]
            need_full_swap = cell != state["pushed_cell"]
            need_frame_push = frame != state["pushed_frame"] or need_full_swap
            need_scene_redo = state["scene_dirty"]

        # Empty scene path: no cell selected (startup state) means nothing
        # to render. Just spin until /set delivers a cell. 1/30 matches
        # the steady-state tick rate at the bottom of the loop.
        if cell is None:
            time.sleep(1 / 30)
            continue

        if need_frame_push:
            data = cells[cell]
            is_v2 = data["version"] == 2
            # Hold the lock for the push so /set's _rebuild_scene_node
            # (called from a worker thread) can't race in mid-attribute
            # write. The try/except RuntimeError is a belt-and-braces
            # guard: viser marks the handle internally on .remove() and
            # attr writes to a stale reference raise even under the lock
            # if the C-side removal isn't fully atomic.
            wrote = False
            with lock:
                local_splat = splat
                if local_splat is not None:
                    try:
                        if need_full_swap:
                            # rgb + opacity are static per cell; cov is
                            # static in v1 but gets re-pushed per frame in
                            # v2. The one-time push gets the cell into a
                            # consistent state before the frame loop kicks
                            # in. Cov is K²-scaled by mmap_cell so it lands
                            # inside fp16's normal range for WS transport.
                            local_splat.covariances = _cov_for_frame(data, frame)
                            local_splat.rgbs = np.ascontiguousarray(data["rgb"])
                            local_splat.opacities = np.ascontiguousarray(data["opacity"])
                        elif is_v2:
                            # Per-frame Σᵢ reconstruction so ellipsoids
                            # rotate with the deformation (~1ms for 683k splats).
                            local_splat.covariances = _cov_for_frame(data, frame)
                        local_splat.centers = np.ascontiguousarray(
                            np.asarray(data["frames"][frame]) * _VISER_K
                        )
                        wrote = True
                    except RuntimeError:
                        pass  # handle removed mid-write; next tick retries
                if wrote:
                    state["pushed_cell"] = cell
                    state["pushed_frame"] = frame

        if need_scene_redo:
            data = cells[cell]
            gp = _grid_params_for_bbox(data["bbox_lo"], data["bbox_hi"])
            ctr = ((data["bbox_lo"] + data["bbox_hi"]) * 0.5).astype(float)
            # Same lift-to-z=0 convention as the boot path: grid at z=0,
            # gizmo at z=0, splat node was added with position=(0,0,lift).
            scene_lift = -float(data["bbox_lo"][2])
            fz = 0.0
            # Remove + re-add is more robust than mutating size attrs in
            # place — `GridHandle` only exposes position/visible, not
            # cell_size/section_size, so the existing handle can't be
            # resized live.
            grid.remove()
            new_grid = server.scene.add_grid(
                "ground",
                width=gp["plane_size"],
                height=gp["plane_size"],
                plane="xy",
                cell_size=gp["cell_size"],
                cell_color=_GRID_CELL_RGB,
                section_size=gp["section_size"],
                section_color=_ACCENT_RGB,
                position=(float(ctr[0]), float(ctr[1]), fz),
            )
            gsize = gp["scene_scale"] * 0.05
            gizmo.remove()
            new_gizmo = server.scene.add_frame(
                "gizmo",
                show_axes=False,
                axes_length=gsize,
                axes_radius=gsize * 0.04,
                position=(float(data["bbox_lo"][0]),
                          float(data["bbox_lo"][1]), fz),
            )
            # Reframe initial_camera so newly connecting clients land
            # well; for already-connected clients the React side will
            # POST /camera at the appropriate moment. Per-client camera
            # near/far writes mid-orbit caused silent WS crashes in
            # viser 1.0.20 — we stick to initial_camera here.
            pos, look = _camera_for_bbox(data["bbox_lo"], data["bbox_hi"])
            # Match the lift applied to the splat node so the camera
            # frames the model in its new world-z position.
            pos  = (pos[0],  pos[1],  pos[2]  + scene_lift)
            look = (look[0], look[1], look[2] + scene_lift)
            dist_new = float(np.linalg.norm(np.asarray(pos) - np.asarray(look)))
            server.initial_camera.position = pos
            server.initial_camera.look_at = look
            server.initial_camera.near = _near_for_distance(dist_new, gp["scene_scale"])
            server.initial_camera.far  = _camera_far_for_scene(gp["scene_scale"])

            # Also reframe every already-connected client — initial_camera
            # only takes effect on NEW connections, but the user's tab is
            # already open. Without this, switching from a unit-scale cell
            # to one at world coords (e.g. cluster_6_15 at ~[3460, 29050])
            # leaves the camera at the old origin and the model renders
            # 30 km offscreen. Position + look_at writes are safe at any
            # time; near/far per-client writes are not (viser 1.0.20 bug,
            # see on_client_connect comment) — stick to initial_camera
            # for those.
            try:
                for client in server.get_clients().values():
                    client.camera.position = pos
                    client.camera.look_at = look
                    client.camera.up_direction = (0.0, 0.0, 1.0)
            except Exception as e:
                print(f">>> scene-dirty client camera push failed: {e}")

            grid = new_grid
            gizmo = new_gizmo
            with lock:
                state["scene_dirty"] = False

        time.sleep(1 / 30)

    # Clean shutdown — try to stop viser's WS so the browser sees a
    # proper close frame rather than a TCP reset.
    print("\n>>> viser_headless: stop requested, shutting down…")
    try:
        server.stop()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
