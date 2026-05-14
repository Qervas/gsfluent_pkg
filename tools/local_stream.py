"""Laptop-side Points-mode WebSocket server.

Reads `<cache>/viser/<name>.npz` (the same file `tools/viser_headless.py`
uses) and serves a Points-mode WS stream matching the protocol of the
server's `/api/stream`. Localhost-only so bandwidth is gigabit-LAN to
localhost — effectively unlimited.

Why mmap the .npz instead of a separate frames.bin? Two reasons:
    1. Single source of truth on the laptop. The sync daemon only has
       to mirror one artifact per sequence; Points and Splats modes
       share it.
    2. The .npz already carries `rgb` (the static attrs the WS protocol
       needs to send once per subscribe). frames.bin is xyz-only and
       would require a separate ply read to get rgb anyway.

WebSocket protocol (matches server/gsfluent/api/stream.py — the React
client can't tell the difference):
  S→C: {type: "static_attrs", run_name, n, rgb_b64, R_b64="",
        scales_b64="", opacity_b64=""}
       {type: "frame_meta", run_name, frame_idx, n}
       binary: Float32Array (n,3) xyz
  C→S: {type: "subscribe", run_name}
       {type: "unsubscribe"}
       {type: "load_model", path}    -- unsupported here, returns error

Live-tail / log-replay logic from the server is absent — those only
matter for runs in progress. Local stream is replay-only; live runs
still go through the server's WS at $GSFLUENT_SERVER/api/stream.

Usage:
    python tools/local_stream.py --cache-root work/cache --port 8083
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

_log = logging.getLogger("local_stream")

# No-op constant left in place during a transition: the K scale-up
# used to happen here, but it's now done upstream in
# `tools/fuse_to_full_ply.py` (the --output_source_scale flag, default
# on). Per-frame plys / npzs arrive in source-world coords from fuse;
# Points mode renders them as-is. Must match `tools/viser_headless.py`'s
# `_VISER_K` for camera sync consistency across mode toggle.
_LOCAL_STREAM_K = 1.0


class CellCache:
    """Lazy-load .npz files by name. Mmap'd, so the first touch is cheap
    and subsequent frame reads pay only the page-in cost.

    Re-reads from disk on each `get()` whose underlying file is newer
    than what we have in memory — that's how the sync daemon's renames
    propagate without us needing an explicit /reload IPC for this service.
    (The viser_headless side does have /reload — same idea, different
    contract.)"""

    def __init__(self, viser_dir: Path) -> None:
        self.viser_dir = viser_dir
        # name → (mtime_at_load, dict-of-arrays)
        self._cache: dict[str, tuple[float, dict]] = {}

    def get(self, name: str) -> Optional[dict]:
        path = self.viser_dir / f"{name}.npz"
        if not path.is_file():
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(name)
        if cached is not None and abs(cached[0] - mtime) < 0.5:
            return cached[1]
        # (Re-)load. mmap_mode keeps RAM bounded.
        try:
            data = np.load(path, mmap_mode="r")
        except Exception as e:
            _log.warning("failed to mmap %s: %s", path, e)
            return None
        # Defensive: the schema we expect has at least these arrays.
        for k in ("frames", "rgb"):
            if k not in data.files:
                _log.warning("%s missing required array '%s'", path, k)
                return None
        self._cache[name] = (mtime, data)
        return data

    def names(self) -> list[str]:
        return [p.stem for p in sorted(self.viser_dir.glob("*.npz"))]


async def _send_static_attrs(ws: WebSocket, run_name: str, rgb: np.ndarray) -> None:
    """Send the once-per-subscribe static-attrs payload. The Three.js
    Points renderer only consumes rgb, so we emit the other fields as
    empty strings to match the protocol shape without paying for the
    24+ MB of R/scales/opacity bytes that the renderer doesn't read."""
    rgb_b = np.ascontiguousarray(rgb).tobytes()
    await ws.send_json({
        "type": "static_attrs",
        "run_name": run_name,
        "n": int(rgb.shape[0]),
        "R_b64":       "",
        "scales_b64":  "",
        "rgb_b64":     base64.b64encode(rgb_b).decode("ascii"),
        "opacity_b64": "",
    })


async def _pump_cell(ws: WebSocket, run_name: str, data: dict) -> None:
    """Burst-send every frame in playback order. Localhost is fast enough
    to fire them all back-to-back; the React side accumulates them in a
    Map<frame_idx, Float32Array> for scrub-friendly playback. We yield
    occasionally so WS keepalives + disconnect handling stay responsive
    on very large sequences."""
    await _send_static_attrs(ws, run_name, np.asarray(data["rgb"]))
    frames = data["frames"]
    n_frames = frames.shape[0]
    n_splats = frames.shape[1]
    for idx in range(n_frames):
        try:
            # Apply the same K-scale viser uses so Points and Splats
            # modes share a single world coord system. Otherwise the
            # camera sync at mode toggle would jump by a factor of K.
            xyz = np.ascontiguousarray(
                np.asarray(frames[idx], dtype=np.float32) * _LOCAL_STREAM_K
            )
        except Exception as e:
            _log.warning("frame %d of %s read failed: %s", idx, run_name, e)
            continue
        await ws.send_json({
            "type": "frame_meta",
            "run_name": run_name,
            "frame_idx": idx,
            "n": int(n_splats),
        })
        await ws.send_bytes(xyz.tobytes())
        # Yield every ~16 frames so disconnects abort the burst quickly.
        # (At 12 MB/frame and gigabit-localhost throughput this is plenty.)
        if (idx & 0xF) == 0:
            await asyncio.sleep(0)


def build_app(cache_root: Path) -> FastAPI:
    """Construct the FastAPI app. Separated out so tests can mount it
    without going through main()."""
    cache = CellCache(cache_root / "viser")

    app = FastAPI()
    # The React SPA is loaded from the server origin; the WS at this
    # localhost port is cross-origin from the SPA's POV. Allow any
    # origin. Combined with the host=127.0.0.1 bind in main(), this is
    # safe in practice: only processes running as the same user on the
    # laptop can reach the port, so the wildcard origin is bounded by
    # the loopback ACL rather than by HTTP CORS. NEVER expose this
    # service on 0.0.0.0 without rethinking access control.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "cache_root": str(cache_root.resolve()),
            "cells_available": cache.names(),
        }

    @app.websocket("/api/stream")
    async def stream(ws: WebSocket) -> None:
        await ws.accept()
        sub_task: Optional[asyncio.Task] = None
        try:
            while True:
                msg = await ws.receive_json()
                kind = msg.get("type")
                if kind == "subscribe":
                    # Wait for the previous pump to fully unwind before
                    # spawning a new one — otherwise an in-flight
                    # `await ws.send_bytes(...)` from the old pump can
                    # interleave with the new pump's `static_attrs` /
                    # `frame_meta` frames on the wire, corrupting the
                    # client-side state machine. Cancel + await is the
                    # only way to guarantee a clean handoff.
                    if sub_task is not None:
                        sub_task.cancel()
                        try:
                            await sub_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        sub_task = None
                    run_name = msg.get("run_name")
                    if not isinstance(run_name, str):
                        await ws.send_json({
                            "type": "error", "code": "bad_subscribe",
                            "run_name": "", "message": "missing run_name",
                        })
                        continue
                    data = cache.get(run_name)
                    if data is None:
                        await ws.send_json({
                            "type": "error", "code": "run_not_found",
                            "run_name": run_name,
                            "message": (
                                f"no .npz at {cache_root}/viser/{run_name}.npz. "
                                "Wait for the sync daemon to mirror it from the server."
                            ),
                        })
                        continue
                    sub_task = asyncio.create_task(_pump_cell(ws, run_name, data))
                elif kind == "unsubscribe":
                    if sub_task is not None:
                        sub_task.cancel()
                        try:
                            await sub_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    sub_task = None
                elif kind == "load_model":
                    # Model previews need access to the original 3DGS ply,
                    # which lives on the server. Tell the client where to
                    # go instead of silently dropping the message.
                    await ws.send_json({
                        "type": "error",
                        "code": "load_model_unsupported_local",
                        "path": msg.get("path", ""),
                        "message": (
                            "load_model is server-only in the split-topology "
                            "deployment. Point the WebSocket at "
                            "$GSFLUENT_SERVER/api/stream for model previews."
                        ),
                    })
                else:
                    await ws.send_json({
                        "type": "error",
                        "code": "unknown_message",
                        "run_name": msg.get("run_name", ""),
                        "message": f"unknown type: {kind!r}",
                    })
        except WebSocketDisconnect:
            pass
        finally:
            if sub_task is not None:
                sub_task.cancel()

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--cache-root", required=True, type=Path,
                    help="Local cache root (must contain a viser/ subdir)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("LOCAL_STREAM_PORT", 8083)))
    ap.add_argument("--host", default="127.0.0.1",
                    help="Bind host. Default 127.0.0.1 — this service is "
                         "intended for the browser running on the same laptop.")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()

    cache_root = args.cache_root.resolve()
    if not (cache_root / "viser").is_dir():
        print(f"WARN: {cache_root}/viser/ does not exist yet — sync daemon "
              "will create it on first download.")

    print(f">>> local_stream: cache={cache_root} listening ws://{args.host}:{args.port}/api/stream")
    app = build_app(cache_root)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
