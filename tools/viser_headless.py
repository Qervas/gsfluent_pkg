"""Headless viser splat renderer — controlled entirely via HTTP.

Strips viser's built-in GUI (no internal cell dropdown, no play/pause
button, no frame slider) and exposes a small HTTP control API on a
sidecar port. The React workbench drives everything — sequence
selection, frame index, playback, camera — via that API. Viser is
reduced to "splat renderer service".

Endpoints (port 8092 by default, configurable):
    POST /set     body={"cell": str?, "frame": int?}     advance playback
    POST /camera  body={"position": [x,y,z], "target": [x,y,z]}  align viewport
    GET  /state   → {"cell", "frame", "n_frames", "cells", "bbox": {...}}
    GET  /camera  → {"position": [...], "target": [...], "wxyz": [...]}

The /set endpoint is fire-and-forget; it returns the resolved state but
the actual GPU upload happens on viser's render thread on its next tick.
Latency is whatever viser's WS push + browser render takes (~1 frame).

Usage:
    python tools/viser_headless.py --npz_dir work/cache/viser
"""
from __future__ import annotations

import argparse
import re
import signal
import threading
import time
from pathlib import Path

import numpy as np
import uvicorn
import viser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Strict-allowlist regex for any user-supplied identifier that becomes
# part of a filesystem path. Library sequence names already pass through
# this on the server side; we enforce again here because the laptop
# might be talking to a hostile or buggy server. Reject anything with
# `..`, `/`, spaces, or shell metas.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


# Workbench dark palette (mirrors frontend/tailwind.config.js). Keeping
# this in sync visually means the iframe inside the React workbench
# doesn't look like a foreign element pasted in. RGB tuples are 0-255.
_CANVAS_RGB    = (13, 17, 23)     # tailwind `canvas`     #0d1117
_GRID_CELL_RGB = (33, 38, 45)     # tailwind `border`     #21262d
_ACCENT_RGB    = (34, 211, 238)   # tailwind `accent`     #22d3ee

# No-op constant left in place during a transition: the K scale-up
# used to happen here, but it's now done upstream in
# `tools/fuse_to_full_ply.py` (or `tools/sequence_to_viser_npz.py`)
# so the per-frame plys/npzs already arrive in source-world
# coordinates. Setting K=1 means viser_headless renders whatever is
# in the .npz without rewriting it.
_VISER_K = 1.0


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


def _quats_to_R(quats: np.ndarray) -> np.ndarray:
    """Batched quaternion (N,4 with w,x,y,z) → (N,3,3) rotation matrices.

    Inputs are expected unit-normalized (sequence_to_viser_npz.py
    normalizes when writing v2). Matches the math in
    `tools/sequence_to_viser_npz.py:_quat_to_R` so v2 cov reconstruction
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

    Look-at = bbox center. Position = bbox center + (1, 1, 0.7) × scene_scale,
    which roughly mirrors the React Canvas default (`position=[5,5,6]`)
    when sceneScale ≈ 7. The +Z component is smaller than +X/+Y so the
    camera looks slightly *down* on the model — most fluid/destruction
    scenes read better from above-eye level."""
    center = ((lo + hi) * 0.5).astype(float)
    scene_scale = float(np.maximum(hi - lo, 1e-6).max())
    offset = np.array([1.0, 1.0, 0.7], dtype=float) * scene_scale * 0.8
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
    args = p.parse_args()

    npz_root = Path(args.npz_dir)
    npz_paths = sorted(npz_root.glob("*.npz"))
    if not npz_paths:
        print(f"ERROR: no .npz in {npz_root}")
        return 2

    print(f"mmap-loading {len(npz_paths)} cells from {npz_root}...")
    cells: dict[str, dict] = {}
    for path in npz_paths:
        cells[path.stem] = mmap_cell(path)
        c = cells[path.stem]
        print(f"  {path.stem}: {c['frames'].shape}  "
              f"bbox=({c['bbox_lo']}, {c['bbox_hi']})")

    # --- viser scene -----------------------------------------------------
    server = viser.ViserServer(port=args.viser_port)

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
    # World axes overlay (the big +X/+Y/+Z triad at world origin) — off;
    # we add a smaller frame at the scene's floor corner so the iframe
    # still carries an orientation cue without dominating the view.
    server.scene.world_axes.visible = False

    # Bootstrap with the first cell. Centers K-scaled to keep geometry
    # self-consistent with the cov scaling done in mmap_cell.
    cur_name = next(iter(cells))
    cur = cells[cur_name]
    splat = server.scene.add_gaussian_splats(
        "splat",
        centers=np.ascontiguousarray(
            np.asarray(cur["frames"][0]) * _VISER_K
        ),
        covariances=_cov_for_frame(cur, 0),       # already K²-scaled
        rgbs=np.ascontiguousarray(cur["rgb"]),
        opacities=np.ascontiguousarray(cur["opacity"]),
    )

    # Adaptive grid + small floor-corner gizmo, both per-cell — when a
    # different sequence is loaded the grid + gizmo reposition with the
    # new bbox. Storing the handles lets us mutate them in place.
    grid_params = _grid_params_for_bbox(cur["bbox_lo"], cur["bbox_hi"])
    bbox_center = ((cur["bbox_lo"] + cur["bbox_hi"]) * 0.5).astype(float)
    floor_z = float(cur["bbox_lo"][2])
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
        show_axes=True,
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
    cur_scale = grid_params["scene_scale"]
    cam_dist0 = float(np.linalg.norm(np.asarray(pos0) - np.asarray(look0)))
    server.initial_camera.position = pos0
    server.initial_camera.look_at = look0
    server.initial_camera.up = (0.0, 0.0, 1.0)
    server.initial_camera.fov = float(np.deg2rad(50.0))   # match React's fov=50
    server.initial_camera.near = _near_for_distance(cam_dist0, cur_scale)
    server.initial_camera.far  = _camera_far_for_scene(cur_scale)

    # Shared state between control API and the render thread.
    state = {
        "cell": cur_name,
        "frame": 0,
        "pushed_cell": cur_name,
        "pushed_frame": -1,
        # Cached last-known camera so the React side can read it via
        # GET /camera at mode-toggle time without having to subscribe to
        # viser's own WS. Updated by the on-update callback below.
        "camera": {
            "position": list(pos0),
            "target":   list(look0),
            "wxyz":     [1.0, 0.0, 0.0, 0.0],
        },
        # If True, the next render-loop tick should also push grid/gizmo
        # repositioning + a fresh initial_camera. Set on cell-swap.
        "scene_dirty": True,
    }
    lock = threading.Lock()

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
        with lock:
            if body.cell is not None:
                if body.cell not in cells:
                    return {"ok": False, "error": f"unknown cell: {body.cell}",
                            "cells": list(cells)}
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
            if body.frame is not None:
                n = cells[state["cell"]]["frames"].shape[0]
                state["frame"] = max(0, min(int(body.frame), n - 1))
            return {"ok": True, "cell": state["cell"], "frame": state["frame"]}

    @api.get("/state")
    def get_state() -> dict:
        with lock:
            cur_c = cells[state["cell"]]
            return {
                "cell": state["cell"],
                "frame": state["frame"],
                "n_frames": cur_c["frames"].shape[0],
                "cells": list(cells),
                "bbox": {
                    "lo": cur_c["bbox_lo"].tolist(),
                    "hi": cur_c["bbox_hi"].tolist(),
                },
            }

    @api.post("/reload")
    def reload_cell(cell: str | None = None) -> dict:
        """Re-mmap a cell's .npz from disk.

        Called by tools/sync_daemon.py after it downloads a fresh copy.
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
            host="0.0.0.0",
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

        if need_frame_push:
            data = cells[cell]
            is_v2 = data["version"] == 2
            if need_full_swap:
                # rgb + opacity are static per cell; cov is static in v1 but
                # gets re-pushed per frame in v2 (see below). For v1 we push
                # cov here as the static attr; v2 will push it again per
                # frame, so the one-time push is just to get the cell into
                # a consistent state before the frame loop kicks in.
                # Cov is already K²-scaled by mmap_cell so it lands inside
                # float16's normal range when viser casts for WS transport.
                splat.covariances = _cov_for_frame(data, frame)
                splat.rgbs = np.ascontiguousarray(data["rgb"])
                splat.opacities = np.ascontiguousarray(data["opacity"])
            elif is_v2:
                # Per-frame Σᵢ reconstruction. Recomputed every push so the
                # ellipsoids rotate with the deformation; otherwise splats
                # smear during motion. Cost: ~1 ms for 683k splats on CPU.
                splat.covariances = _cov_for_frame(data, frame)
            # Centers: K-scaled to stay in lockstep with the cov scaling
            # (geometry self-consistent). The multiply allocates a fresh
            # ~8 MB array per frame; trivial at 30 fps.
            splat.centers = np.ascontiguousarray(
                np.asarray(data["frames"][frame]) * _VISER_K
            )
            with lock:
                state["pushed_cell"] = cell
                state["pushed_frame"] = frame

        if need_scene_redo:
            data = cells[cell]
            gp = _grid_params_for_bbox(data["bbox_lo"], data["bbox_hi"])
            ctr = ((data["bbox_lo"] + data["bbox_hi"]) * 0.5).astype(float)
            fz = float(data["bbox_lo"][2])
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
                show_axes=True,
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
            dist_new = float(np.linalg.norm(np.asarray(pos) - np.asarray(look)))
            server.initial_camera.position = pos
            server.initial_camera.look_at = look
            server.initial_camera.near = _near_for_distance(dist_new, gp["scene_scale"])
            server.initial_camera.far  = _camera_far_for_scene(gp["scene_scale"])

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
