"""WebSocket events:

  Server → Client (JSON):
    { type: "static_attrs", run_name, n, R_b64, scales_b64, rgb_b64, opacity_b64 }
      -- sent ONCE per run on the first frame
    { type: "frame_meta", run_name, frame_idx, n }
      -- emitted right before the matching binary message
    binary message after frame_meta: Float32Array of (n, 3) xyz
    { type: "error", code, run_name, message }
      -- emitted on subscription-level failures (run_not_found,
      snapshot_failed, watch_failed)

  Client → Server (JSON):
    { type: "subscribe", run_name }
    { type: "unsubscribe" }
"""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from watchfiles import awatch

from ..core import runner
from ..core.frame_stream import parse_frame_xyz, parse_static_attrs
from ..core.runner import _log_task_exception

_log = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/api/stream")
async def stream(ws: WebSocket):
    await ws.accept()
    sub_task: asyncio.Task | None = None
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "subscribe":
                if sub_task is not None:
                    sub_task.cancel()
                sub_task = asyncio.create_task(_pump(ws, msg["run_name"]))
                sub_task.add_done_callback(_log_task_exception)
            elif msg.get("type") == "unsubscribe":
                if sub_task is not None:
                    sub_task.cancel()
                sub_task = None
    except WebSocketDisconnect:
        pass
    finally:
        if sub_task is not None:
            sub_task.cancel()


async def _pump(ws: WebSocket, run_name: str) -> None:
    """Tail the frames/ subdir of `run_name`'s fused dir, sending each
    new frame_*.ply as it appears. Sends static_attrs on the first
    frame that carries the full 3DGS attribute set."""
    run_dir = runner.FUSED_DIR / run_name
    if not run_dir.exists():
        try:
            await ws.send_json({
                "type": "error",
                "code": "run_not_found",
                "run_name": run_name,
                "message": f"run_dir does not exist: {run_dir}",
            })
        except WebSocketDisconnect:
            pass
        return

    sent: set[str] = set()
    sent_static = False
    # Initial snapshot — pick up frames already on disk.
    try:
        for f in sorted(run_dir.glob("frames/frame_*.ply")):
            sent_static = await _send(ws, run_name, f, sent, sent_static)
    except WebSocketDisconnect:
        return
    except Exception as e:
        _log.exception("error during initial frame snapshot for %s", run_name)
        # Try to inform the client; ignore further failures.
        try:
            await ws.send_json({"type": "error", "code": "snapshot_failed",
                                "run_name": run_name, "message": str(e)})
        except Exception:
            pass

    # Watch for new frames. awatch raises asyncio.CancelledError on cancel.
    try:
        async for changes in awatch(run_dir):
            for _, p_str in changes:
                p = Path(p_str)
                if p.match("frames/frame_*.ply"):
                    try:
                        sent_static = await _send(ws, run_name, p, sent, sent_static)
                    except WebSocketDisconnect:
                        return
                    except Exception as e:
                        _log.warning("skipping unreadable frame %s: %s", p, e)
                        continue
    except (FileNotFoundError, PermissionError) as e:
        _log.warning("watch terminated for %s: %s", run_name, e)
        try:
            await ws.send_json({"type": "error", "code": "watch_failed",
                                "run_name": run_name, "message": str(e)})
        except Exception:
            pass
    except asyncio.CancelledError:
        raise


async def _send(ws: WebSocket, run_name: str, ply: Path,
                sent: set[str], sent_static_already: bool) -> bool:
    """Send one frame; on first call, also send static_attrs.
    Returns the new value of `sent_static_already`."""
    if ply.name in sent:
        return sent_static_already
    try:
        # Mid-write heuristic: production fused plys (200k+ splats with full
        # 3DGS attrs) are at least ~6 MB. Tests using tiny synthetic plys may
        # need to bypass this check by writing >1024 bytes of header padding.
        if ply.stat().st_size < 1024:
            return sent_static_already   # mid-write
    except FileNotFoundError:
        return sent_static_already
    if not sent_static_already:
        attrs = parse_static_attrs(ply)
        if attrs is not None:
            await ws.send_json({
                "type": "static_attrs",
                "run_name": run_name,
                "n": int(attrs["n"]),
                "R_b64":      base64.b64encode(attrs["R"].tobytes()).decode("ascii"),
                "scales_b64": base64.b64encode(attrs["scales"].tobytes()).decode("ascii"),
                "rgb_b64":    base64.b64encode(attrs["rgb"].tobytes()).decode("ascii"),
                "opacity_b64":base64.b64encode(attrs["opacity"].tobytes()).decode("ascii"),
            })
            sent_static_already = True
    xyz = parse_frame_xyz(ply)
    idx = int(ply.stem.split("_")[1])
    await ws.send_json({
        "type": "frame_meta", "run_name": run_name,
        "frame_idx": idx, "n": int(xyz.shape[0]),
    })
    await ws.send_bytes(xyz.tobytes())
    sent.add(ply.name)
    return sent_static_already
