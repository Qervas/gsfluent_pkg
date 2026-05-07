"""WebSocket events:

  Server → Client (JSON):
    { type: "static_attrs", run_name, n, R_b64, scales_b64, rgb_b64, opacity_b64 }
      -- sent ONCE per run on the first frame
    { type: "frame_meta", run_name, frame_idx, n }
      -- emitted right before the matching binary message
    binary message after frame_meta: Float32Array of (n, 3) xyz
    { type: "error", code, run_name, message }
      -- emitted on subscription-level failures (run_not_found,
      snapshot_failed, watch_failed) and on load_model failures
      (model_not_found, model_parse_failed); for the latter the
      "run_name" field is replaced by "path".

  Client → Server (JSON):
    { type: "subscribe", run_name }
    { type: "unsubscribe" }
    { type: "load_model", path }
      -- render <path>/point_cloud/iteration_<N>/point_cloud.ply
      (highest N) as a single static frame; cancels any active
      run subscription first.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path

import numpy as np
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
            elif msg.get("type") == "load_model":
                # Model preview replaces any active run subscription —
                # don't conflate the two streams of frames.
                if sub_task is not None:
                    sub_task.cancel()
                sub_task = None
                await _send_model_snapshot(ws, Path(msg["path"]))
    except WebSocketDisconnect:
        pass
    finally:
        if sub_task is not None:
            sub_task.cancel()


async def _pump(ws: WebSocket, run_name: str) -> None:
    """Tail `run_name`'s fused dir, sending each new frame_*.ply as it
    appears. Frames may live directly in the run dir (sim_one.sh's
    output) or in a frames/ subdir (newer pipeline). Sends static_attrs
    on the first frame that carries the full 3DGS attribute set."""
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
    # Initial snapshot — look in BOTH the run root AND frames/ subdir.
    # sim_one.sh writes frames directly into the run dir; the new pipeline
    # may use a frames/ subdir going forward. Support both.
    try:
        for f in sorted(_list_frame_plys(run_dir)):
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
                if _is_frame_ply(p):
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


def _list_frame_plys(run_dir: Path) -> list[Path]:
    """Frames may live in <run_dir>/frame_*.ply (sim_one.sh's output) or
    <run_dir>/frames/frame_*.ply (newer pipeline). Return both, deduped
    by stem (frame_N), with the run-root copy winning on conflict."""
    out: list[Path] = []
    out.extend(run_dir.glob("frame_*.ply"))
    out.extend(run_dir.glob("frames/frame_*.ply"))
    seen: set[str] = set()
    deduped: list[Path] = []
    for p in out:
        if p.stem not in seen:
            deduped.append(p)
            seen.add(p.stem)
    return deduped


def _is_frame_ply(p: Path) -> bool:
    """True for any frame_*.ply landing in a watched run_dir, regardless
    of whether it's at the root or in a frames/ subdir."""
    return p.name.startswith("frame_") and p.name.endswith(".ply")


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


async def _safe_send_json(ws: WebSocket, payload: dict) -> None:
    """ws.send_json that swallows disconnect and logs other failures —
    used by _send_model_snapshot, where an error path shouldn't crash
    the outer receive loop."""
    try:
        await ws.send_json(payload)
    except WebSocketDisconnect:
        return
    except Exception:
        _log.exception("ws.send_json failed")


async def _send_model_snapshot(ws: WebSocket, model_path: Path) -> None:
    """Locate <model_path>/point_cloud/iteration_<N>/point_cloud.ply
    (highest N), parse it, and send static_attrs + frame_meta + binary
    xyz exactly like a one-frame run. For xyz-only plys (no full 3DGS
    attribute set) emit a synthetic gray static_attrs payload so the
    SplatScene can still render."""
    if not model_path.is_dir():
        await _safe_send_json(ws, {
            "type": "error", "code": "model_not_found",
            "path": str(model_path),
            "message": f"model dir does not exist: {model_path}",
        })
        return
    pc_root = model_path / "point_cloud"
    if not pc_root.is_dir():
        await _safe_send_json(ws, {
            "type": "error", "code": "model_not_found",
            "path": str(model_path),
            "message": f"missing point_cloud/ subdir under {model_path}",
        })
        return

    iter_re = re.compile(r"^iteration_(\d+)$")
    candidates: list[tuple[int, Path]] = []
    for it in pc_root.iterdir():
        if not it.is_dir():
            continue
        m = iter_re.match(it.name)
        if m and (it / "point_cloud.ply").is_file():
            candidates.append((int(m.group(1)), it / "point_cloud.ply"))
    if not candidates:
        await _safe_send_json(ws, {
            "type": "error", "code": "model_not_found",
            "path": str(model_path),
            "message": f"no iteration_*/point_cloud.ply under {pc_root}",
        })
        return
    candidates.sort(key=lambda t: -t[0])
    ply_path = candidates[0][1]

    try:
        attrs = parse_static_attrs(ply_path)
        xyz = parse_frame_xyz(ply_path)
    except Exception as e:
        _log.exception("failed to parse model ply at %s", ply_path)
        await _safe_send_json(ws, {
            "type": "error", "code": "model_parse_failed",
            "path": str(model_path), "message": str(e),
        })
        return

    n = int(xyz.shape[0])
    run_name = f"_model:{model_path.name}"

    if attrs is not None:
        await _safe_send_json(ws, {
            "type": "static_attrs",
            "run_name": run_name,
            "n": int(attrs["n"]),
            "R_b64":      base64.b64encode(attrs["R"].tobytes()).decode("ascii"),
            "scales_b64": base64.b64encode(attrs["scales"].tobytes()).decode("ascii"),
            "rgb_b64":    base64.b64encode(attrs["rgb"].tobytes()).decode("ascii"),
            "opacity_b64":base64.b64encode(attrs["opacity"].tobytes()).decode("ascii"),
        })
    else:
        # xyz-only ply (e.g. raw point cloud with no SH/scale/rot fields).
        # Synthesize a uniform gray static_attrs payload so the SplatScene
        # has something to render — monochrome is fine for a preview.
        rgb = np.full((n, 3), 0.6, dtype=np.float32)
        opacity = np.full(n, 1.0, dtype=np.float32)
        scales = np.full((n, 3), 0.005, dtype=np.float32)
        R = np.tile(np.eye(3, dtype=np.float32), (n, 1, 1))
        await _safe_send_json(ws, {
            "type": "static_attrs",
            "run_name": run_name,
            "n": n,
            "R_b64":      base64.b64encode(R.tobytes()).decode("ascii"),
            "scales_b64": base64.b64encode(scales.tobytes()).decode("ascii"),
            "rgb_b64":    base64.b64encode(rgb.tobytes()).decode("ascii"),
            "opacity_b64":base64.b64encode(opacity.tobytes()).decode("ascii"),
        })

    await _safe_send_json(ws, {
        "type": "frame_meta",
        "run_name": run_name,
        "frame_idx": 0, "n": n,
    })
    try:
        await ws.send_bytes(xyz.tobytes())
    except WebSocketDisconnect:
        return
    except Exception:
        _log.exception("ws.send_bytes failed for model snapshot %s", model_path)
