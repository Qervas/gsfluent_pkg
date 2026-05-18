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

from ..core import library as lib
from ..core import runner
from ..core.frame_stream import (
    PackedReader,
    parse_frame_xyz,
    parse_static_attrs,
)
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
                # Allowlist the path against the registry. Without this
                # the WS lets the client read any 3DGS directory on the
                # server (same hole as GET /api/models/file).
                from ..core import models as _models
                requested = Path(msg["path"]).resolve()
                known = {
                    Path(e["path"]).resolve()
                    for e in _models.list_models() if e.get("path")
                }
                if requested not in known:
                    await ws.send_json({
                        "type": "error",
                        "code": "model_not_found",
                        "path": str(requested),
                        "message": "model path is not registered",
                    })
                    continue
                await _send_model_snapshot(ws, requested)
    except WebSocketDisconnect:
        pass
    finally:
        if sub_task is not None:
            sub_task.cancel()


_SAFE_RUN_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _resolve_run_dir(run_name: str) -> Path | None:
    """Locate the on-disk dir for `run_name`. Library wins; legacy
    fused dir only used as a fallback (tests + pre-migration data).
    Returns None if the run isn't found anywhere.

    `run_name` is WebSocket-supplied. Reject anything but plain
    identifiers — otherwise `run_name="../../etc"` walks outside
    SEQUENCES_DIR / FUSED_DIR and lets the client read arbitrary dirs
    via _pump's frame-tail loop.
    """
    if not _SAFE_RUN_NAME.match(run_name):
        return None
    seq_dir = lib.SEQUENCES_DIR / run_name
    if seq_dir.is_dir():
        return seq_dir
    legacy = runner.FUSED_DIR / run_name
    if legacy.is_dir():
        return legacy
    return None


async def _pump(ws: WebSocket, run_name: str) -> None:
    """Tail `run_name`'s sequence dir, sending each new frame_*.ply as
    it appears. Canonical layout is `<seq>/frames/frame_*.ply` (Phase 1
    library). The legacy layouts (frames at run root, or in `<run>/`
    only) are still accepted for tests + pre-migration data; we collapse
    them into the same dedup pass.

    Sends static_attrs on the first frame that carries the full 3DGS
    attribute set (frame 0 in the canonical layout)."""
    run_dir = _resolve_run_dir(run_name)
    if run_dir is None:
        try:
            await ws.send_json({
                "type": "error",
                "code": "run_not_found",
                "run_name": run_name,
                "message": f"run dir does not exist for {run_name}",
            })
        except WebSocketDisconnect:
            pass
        return

    # Replay the persisted run.log so the user sees output even after the
    # subprocess is gone. For an errored run this is the only way to find
    # out WHY it failed — the in-memory Run.log_lines disappear with the
    # process. New lines appended during a live run still get pumped via
    # the awatch loop below.
    log_path = run_dir / "run.log"
    if log_path.is_file():
        try:
            with log_path.open() as fh:
                for line in fh:
                    await ws.send_json({
                        "type": "log",
                        "run_name": run_name,
                        "line": line.rstrip("\n"),
                    })
        except WebSocketDisconnect:
            return
        except Exception as e:
            _log.warning("failed to replay run.log for %s: %s", run_name, e)
    # Tell the client the run's terminal status if the manifest knows it.
    # Without this, past errored runs stay forever in "running" state on
    # the client and the StatusStrip never settles into the replay layout.
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            import json as _json
            with manifest_path.open() as fh:
                m = _json.load(fh)
            status = m.get("status")
            if status in ("done", "error", "cancelled"):
                await ws.send_json({
                    "type": "status",
                    "run_name": run_name,
                    "state": status,
                })
        except WebSocketDisconnect:
            return
        except Exception as e:
            _log.warning("failed to read manifest for %s: %s", run_name, e)

    sent: set[str] = set()
    sent_static = False
    # Initial snapshot. Prefer packed `frames.bin` (mmap-fast, 30× smaller
    # on disk) if it exists; fall back to per-ply iteration otherwise so
    # legacy sequences keep working.
    #
    # PackedReader owns an open file descriptor and mmap. Without
    # explicit close on WS disconnect we'd leak both per connection
    # (relying on __del__ at GC time is non-deterministic under WS
    # exception paths). The outer try/finally below pairs with this open.
    packed = PackedReader.maybe_open(run_dir)
    try:
        if packed is not None:
            # Static attrs come from the bootstrap frame_0000.ply — packed
            # data only carries xyz. The ply still lives at frames/.
            bootstrap_ply = run_dir / "frames" / "frame_0000.ply"
            if not bootstrap_ply.is_file():
                bootstrap_ply = run_dir / "frame_0000.ply"
            if bootstrap_ply.is_file() and not sent_static:
                attrs = parse_static_attrs(bootstrap_ply)
                if attrs is not None:
                    await ws.send_json({
                        "type": "static_attrs",
                        "run_name": run_name,
                        "n": int(attrs["n"]),
                        "R_b64":      "",
                        "scales_b64": "",
                        "rgb_b64":    base64.b64encode(attrs["rgb"].tobytes()).decode("ascii"),
                        "opacity_b64":"",
                    })
                    sent_static = True
            for idx in range(packed.n_frames):
                xyz = packed.xyz(idx)
                await ws.send_json({
                    "type": "frame_meta", "run_name": run_name,
                    "frame_idx": idx, "n": int(xyz.shape[0]),
                })
                await ws.send_bytes(xyz.tobytes())
                # Synthesize a frame name so the dedupe set + downstream
                # mid-stream watch still see the same identity space.
                sent.add(f"frame_{idx:04d}.ply")
        else:
            # Legacy per-ply path. Numeric-by-frame-idx sort. Path's default
            # sort is lexicographic — orders frame_10 before frame_2 — which
            # makes the initial snapshot arrive out of playback order.
            for f in sorted(_list_frame_plys(run_dir), key=_frame_idx_for_sort):
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

    # Track how many bytes of run.log we've already sent so live tails
    # only emit new lines.
    log_offset = log_path.stat().st_size if log_path.is_file() else 0

    # Watch for new frames AND log appends. awatch raises CancelledError on cancel.
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
                elif p.name == "run.log":
                    # Tail-read new lines appended since last offset.
                    try:
                        with log_path.open() as fh:
                            fh.seek(log_offset)
                            chunk = fh.read()
                            log_offset = fh.tell()
                        for line in chunk.splitlines():
                            await ws.send_json({
                                "type": "log",
                                "run_name": run_name,
                                "line": line,
                            })
                    except WebSocketDisconnect:
                        return
                    except Exception as e:
                        _log.warning("failed to tail run.log for %s: %s", run_name, e)
                elif p.name == "manifest.json":
                    # Manifest update — re-read status and emit if terminal.
                    try:
                        import json as _json
                        with manifest_path.open() as fh:
                            m = _json.load(fh)
                        status = m.get("status")
                        if status in ("done", "error", "cancelled"):
                            await ws.send_json({
                                "type": "status",
                                "run_name": run_name,
                                "state": status,
                            })
                    except WebSocketDisconnect:
                        return
                    except Exception as e:
                        _log.warning("failed to re-read manifest for %s: %s", run_name, e)
    except (FileNotFoundError, PermissionError) as e:
        _log.warning("watch terminated for %s: %s", run_name, e)
        try:
            await ws.send_json({"type": "error", "code": "watch_failed",
                                "run_name": run_name, "message": str(e)})
        except Exception:
            pass
    except asyncio.CancelledError:
        # Re-raise so the parent task sees cancellation, but make sure
        # we close the mmap'd reader first. Without this the fd + page
        # cache stay pinned for the lifetime of the parent process.
        if packed is not None:
            try:
                packed.close()
            except Exception:
                pass
        raise
    finally:
        # Catches the normal-exit and `return`-after-disconnect paths.
        # Idempotent: close() guards against double-close internally.
        if packed is not None:
            try:
                packed.close()
            except Exception:
                pass


def _list_frame_plys(run_dir: Path) -> list[Path]:
    """Return the run's frame_*.ply files.

    Canonical layout (post-Phase-1 library): `<run_dir>/frames/frame_*.ply`.
    Legacy layouts (frames at the run root) are still accepted for tests
    and any pre-migration data — deduplicated by stem so we don't emit a
    frame twice if both copies exist."""
    out: list[Path] = []
    out.extend(run_dir.glob("frames/frame_*.ply"))
    out.extend(run_dir.glob("frame_*.ply"))
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


def _frame_idx_for_sort(p: Path) -> int:
    """Parse the integer N from a frame_<N>.ply filename. Used to sort the
    initial snapshot in numeric (playback) order rather than lexicographic
    order. Returns a large sentinel for malformed names so they sort last
    instead of crashing the snapshot send."""
    try:
        return int(p.stem.split("_", 1)[1])
    except (IndexError, ValueError):
        return 10**9


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
            # Only send fields the Three.js Points renderer actually consumes.
            # R + opacity were ~33 MB + 3.6 MB base64 for a 683k-splat scene
            # and pushed the static_attrs JSON over WebSocket message-size
            # limits in some browsers — but neither is rendered. scales is
            # also unused (point size is bbox-derived). Send only rgb.
            await ws.send_json({
                "type": "static_attrs",
                "run_name": run_name,
                "n": int(attrs["n"]),
                "R_b64":      "",
                "scales_b64": "",
                "rgb_b64":    base64.b64encode(attrs["rgb"].tobytes()).decode("ascii"),
                "opacity_b64":"",
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

    # Only send fields the Three.js Points renderer actually consumes (rgb).
    # For a 683k-splat model, R alone was 24 MB raw / 33 MB base64 — pushing
    # the static_attrs JSON past WebSocket message-size limits in some
    # browsers and silently dropping the message. Result: empty viewport.
    # The Points renderer doesn't use R or opacity, and point size is now
    # bbox-derived (so scales is unused too). Send rgb only.
    if attrs is not None:
        await _safe_send_json(ws, {
            "type": "static_attrs",
            "run_name": run_name,
            "n": int(attrs["n"]),
            "R_b64":      "",
            "scales_b64": "",
            "rgb_b64":    base64.b64encode(attrs["rgb"].tobytes()).decode("ascii"),
            "opacity_b64":"",
        })
    else:
        # xyz-only ply (e.g. raw point cloud with no SH/scale/rot fields).
        # Synthesize a uniform gray rgb so the SplatScene has color to use.
        rgb = np.full((n, 3), 0.6, dtype=np.float32)
        await _safe_send_json(ws, {
            "type": "static_attrs",
            "run_name": run_name,
            "n": n,
            "R_b64":      "",
            "scales_b64": "",
            "rgb_b64":    base64.b64encode(rgb.tobytes()).decode("ascii"),
            "opacity_b64":"",
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
