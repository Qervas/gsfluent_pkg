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
    python frontend/python/viser_headless.py --cache-dir work/cache/viser

The cache directory holds per-sequence .gsq files (visual-lossless
streamable cache); .npz is fully retired.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json as _json
import os as _os
import re
import signal
import sys as _sys
import threading
import time
from pathlib import Path
from typing import Callable

import httpx
import numpy as np
import uvicorn
import viser
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Local sibling module — no upward dep on viser. Owns the per-cell
# sliding-window ring buffer that replaces the prior "decode every
# frame into RAM" model.
import sys as _sys_for_import
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in _sys_for_import.path:
    _sys_for_import.path.insert(0, str(_THIS_DIR))
import splat_ring as _splat_ring   # noqa: E402  (path-tweak above)
SplatRing = _splat_ring.SplatRing
make_static_cell = _splat_ring.make_static_cell


# ----- structured event emitter (Phase 6) ----------------------------------
#
# Mirrors the JSON shape emitted by gsfluent.observability.jsonlog so
# operators can grep journalctl uniformly across backend + viser_headless
# events. Writes to stderr (not stdout) because the viser library uses
# stdout for its own progress messages.
#
# We vendor a tiny implementation rather than importing
# gsfluent.observability because viser_headless is a standalone client
# script that must remain runnable without the server package on PYTHONPATH.

def _emit_event(event: str, **context):
    """Emit one structured JSON event to stderr.

    Output shape (one line):
        {"ts": "2026-05-22T12:34:56.789Z", "level": "INFO",
         "event": "cell.cache.hit", ...}
    """
    obj = {
        "ts": _dt.datetime.now(_dt.timezone.utc)
              .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": "INFO",
        "event": event,
        "component": "viser_headless",
    }
    for k, v in context.items():
        try:
            _json.dumps(v)
            obj[k] = v
        except (TypeError, ValueError):
            obj[k] = str(v)
    _sys.stderr.write(_json.dumps(obj, separators=(",", ":")) + "\n")
    try:
        _sys.stderr.flush()
    except Exception:
        pass

# ----- bounded cell cache (Phase 7: prevent unbounded RAM growth) -----------
#
# A single decoded sequence cell is 200-400 MB resident (n_splats × n_frames
# × 7 floats × 4 bytes). The original `cells: dict[str, dict]` never
# evicted, so over a long session with 5-10 sequence loads RAM grew to
# 2-4 GB — eventually OOMing the laptop or thrashing swap. This LRU caps
# the live-decoded set; re-clicking an evicted cell triggers a fresh
# decode from the .gsq on disk (Tier 2: ~1-3s) rather than the from-RAM
# Tier 1 path. The active cell is never evicted, so playback never
# stalls because a render-loop tick lost its current cell.
#
# Reads via __getitem__ AND writes via __setitem__ both update MRU
# position. Pure existence checks (__contains__) are passive — they do
# NOT move-to-end, so a `name in cells` poll from /state can't fight
# the eviction order.

class _CellLRU(collections.abc.MutableMapping):
    """OrderedDict-backed LRU. Drop-in for the prior `cells: dict[str, dict]`.

    Constructor args:
      max_size: cap on live cells; <=0 disables eviction (testing only).
      pinned:   optional callable returning the name to never evict
                (typically `lambda: state["cell"]`). Late-bound so the
                cache can be constructed before `state` exists.
      emit:     optional callable matching `_emit_event` for evict events.
    """

    def __init__(
        self,
        max_size: int,
        pinned: Callable[[], str | None] | None = None,
        emit: Callable[..., None] | None = None,
    ) -> None:
        self._od: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self._max = int(max_size)
        self._pinned = pinned
        self._emit = emit

    def __getitem__(self, key: str) -> dict:
        # Mark as MRU on every read. The render loop reads `cells[cell]`
        # every tick at 30Hz, so the currently-rendered cell is naturally
        # held at the MRU end without any explicit pinning.
        v = self._od[key]
        self._od.move_to_end(key)
        return v

    def __setitem__(self, key: str, value: dict) -> None:
        # If the key already exists, this counts as an update + MRU touch.
        # If it's new, it's an insert + MRU touch; we may need to evict.
        is_update = key in self._od
        self._od[key] = value
        self._od.move_to_end(key)
        if not is_update:
            self._maybe_evict()

    def __delitem__(self, key: str) -> None:
        del self._od[key]

    def __iter__(self):
        return iter(self._od)

    def __len__(self) -> int:
        return len(self._od)

    def __contains__(self, key: object) -> bool:
        # Pure observation — no MRU update. Lets /state polls and the
        # `if cell in cells` guards run without disturbing eviction order.
        return key in self._od

    def _maybe_evict(self) -> None:
        if self._max <= 0 or len(self._od) <= self._max:
            return
        pinned_name = None
        if self._pinned is not None:
            try:
                pinned_name = self._pinned()
            except Exception:
                pinned_name = None
        # Walk from the LRU end forward, skipping the pinned (active)
        # cell. In normal use only one cell is pinned, so the walk is
        # O(1) amortized. If every cell is pinned (impossible today —
        # only one active cell) we'd no-op gracefully.
        for name in list(self._od.keys()):
            if name == pinned_name:
                continue
            evicted = self._od.pop(name)
            evicted_size_hint = None
            try:
                # Ring-backed cell: rough live-RAM proxy = ring size ×
                # n_splats × 28 bytes (xyz f32 + quat f32). Legacy cell:
                # the full frames-array nbytes (the old all-frames-in-RAM
                # estimate).
                ring = evicted.get("ring")
                if ring is not None:
                    try:
                        stats = ring.stats()
                        n_sp = int(ring.n_splats)
                        ring_size = int(stats.get("ring_size", 0))
                        evicted_size_hint = ring_size * n_sp * 28
                    except Exception:
                        evicted_size_hint = None
                else:
                    frames = evicted.get("frames")
                    if frames is not None and hasattr(frames, "nbytes"):
                        evicted_size_hint = int(frames.nbytes)
            except Exception:
                pass
            # Release any background resources (the ring's decoder thread,
            # file handles) before dropping the cell. _close_cell is a
            # late-bound import to keep _CellLRU side-effect-free at
            # class-definition time (the closure resolves at call time).
            try:
                _close_cell(evicted)        # noqa: F821 (defined below)
            except Exception:
                pass
            if self._emit is not None:
                try:
                    self._emit(
                        "cell.cache.evicted",
                        cell=name,
                        max_size=self._max,
                        live=len(self._od),
                        frames_bytes=evicted_size_hint,
                    )
                except Exception:
                    pass
            # One eviction per insert is the contract; loop exits.
            return


# Strict-allowlist regex for any user-supplied identifier that becomes
# part of a filesystem path. Library sequence names already pass through
# this on the server side; we enforce again here because the client
# might be talking to a hostile or buggy server. Reject anything with
# `..`, `/`, spaces, or shell metas.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _local_etag(path: Path) -> str:
    """Compute the weak ETag the server would emit for `path`.

    Format MUST match server/gsfluent/api/sequences.py:_gsq_etag — the
    contract is the literal byte equality of the quoted ETag string.

        '"<size>-<mtime_int>"'

    Recomputed from os.stat() each call; no persistent sidecar file. The
    .gsq cache is small enough (sub-GB) that a stat is free and the
    sidecar maintenance cost would outweigh its benefit.

    Raises FileNotFoundError if path doesn't exist — callers should
    check is_file() first.
    """
    st = path.stat()
    return f'"{st.st_size}-{int(st.st_mtime)}"'


def _base_url_from_full(full_url: str) -> str:
    """Derive the base-layer URL from a full splats.gsq URL.

    .../cache/splats.gsq -> .../cache/base.gsq
    """
    return full_url.rsplit("/", 1)[0] + "/base.gsq"


def _lod_decision(*, full_is_current: bool, base_status: int | None) -> str:
    """Pick the fetch strategy for a cold sync_cell.

    - "full-direct": full file already current on disk -> load it, skip base.
    - "two-tier":    base layer exists (HEAD 200) -> stream base, then full+swap.
    - "full-only":   no base layer -> stream full directly (today's behavior).
    """
    if full_is_current:
        return "full-direct"
    if base_status == 200:
        return "two-tier"
    return "full-only"


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


def _download_gsq_to_disk(url: str, dest: Path, *, timeout: float = 600.0) -> int:
    """Stream a .gsq from `url` to `dest` WITHOUT decoding or publishing it.

    The LOD full-layer path: while the base layer is already playing, we just
    need the full file on disk, then a ring swap. Publishing partial frames
    here would clobber the live base cell and crawl at the network rate — so
    this writes bytes only.

    Writes to <dest>.partial then atomically renames. If a .partial exists,
    resumes via Range (treats a 200 response as 'server ignored Range' and
    restarts from scratch). No proxy (trust_env=False), mirroring the rest of
    the client. Returns the total byte count of the finished file.
    """
    partial = dest.with_suffix(dest.suffix + ".partial")
    headers: dict[str, str] = {}
    mode = "wb"
    if partial.exists() and partial.stat().st_size > 0:
        headers["Range"] = f"bytes={partial.stat().st_size}-"
        mode = "ab"
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True,
                      trust_env=False, headers=headers) as r:
        if r.status_code == 200 and mode == "ab":
            # Server ignored Range — restart from scratch.
            partial.unlink(missing_ok=True)
            mode = "wb"
        elif r.status_code not in (200, 206):
            raise RuntimeError(f"download failed: HTTP {r.status_code}")
        with open(partial, mode) as f:
            for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
    _os.replace(str(partial), str(dest))
    return dest.stat().st_size


def _gsq_dequantize_frame(blob: bytes, n_splats: int,
                          bbox_min: np.ndarray, span: np.ndarray) -> tuple:
    """Decompress one frame chunk into (xyz f32, quat f32) arrays."""
    import zstandard as _zstd
    raw = _zstd.ZstdDecompressor().decompress(blob)
    xyz_i16 = np.frombuffer(raw[: n_splats * 3 * 2], dtype=np.int16).reshape(n_splats, 3)
    quat_i16 = np.frombuffer(
        raw[n_splats * 3 * 2 : n_splats * 3 * 2 * 2], dtype=np.int16,
    ).reshape(n_splats, 3)
    xyz = bbox_min + (xyz_i16.astype(np.float32) + 32768.0) / 65535.0 * span
    qxyz = quat_i16.astype(np.float32) / 32767.0
    qw = np.sqrt(np.clip(1.0 - (qxyz * qxyz).sum(axis=1), 0.0, 1.0))
    quat = np.empty((n_splats, 4), dtype=np.float32)
    quat[:, 0] = qw
    quat[:, 1:4] = qxyz
    return xyz, quat


def parse_gsq_header(buf: bytes) -> dict:
    """Parse the 80-byte .gsq header + frame index.

    Used by the streaming consumer to know which byte ranges to download
    and decode incrementally. Returns enough info to build a cell shell
    that can grow as frames arrive.
    """
    import struct as _struct
    if len(buf) < 80:
        raise ValueError(f"short header: {len(buf)} bytes")
    if buf[:4] != b"GSQ1":
        raise ValueError(f"not a .gsq: magic={buf[:4]!r}")
    (version, n_splats, n_frames) = _struct.unpack_from("<III", buf, 4)
    if version != 1:
        raise ValueError(f"unsupported .gsq version {version}")
    (fps_hint,) = _struct.unpack_from("<f", buf, 16)
    bbox_min = np.frombuffer(buf[20:32], dtype=np.float32).copy()
    bbox_max = np.frombuffer(buf[32:44], dtype=np.float32).copy()
    (static_offset, static_size) = _struct.unpack_from("<QI", buf, 44)

    index_end = 80 + n_frames * 16
    if len(buf) < index_end:
        raise ValueError(f"header read but index incomplete: have {len(buf)} need {index_end}")
    frame_index = []
    for i in range(n_frames):
        off, sz, _r = _struct.unpack_from("<QII", buf, 80 + i * 16)
        frame_index.append((off, sz))
    return {
        "version": version, "n_splats": n_splats, "n_frames": n_frames,
        "fps_hint": fps_hint,
        "bbox_min": bbox_min, "bbox_max": bbox_max,
        "static_offset": static_offset, "static_size": static_size,
        "frame_index": frame_index,
    }


def load_cell_gsq(gsq_path: Path) -> dict:
    """Construct a sliding-window-backed cell for ``gsq_path``.

    The cell carries:
      - ``ring``: a SplatRing managing the on-disk decode
      - ``rgb`` / ``opacity`` / ``scales_sq`` / ``bbox_lo`` / ``bbox_hi``:
        the static attrs the render loop reads directly
      - ``n_frames``: convenience mirror of ``ring.n_frames``

    Frame 0 is pre-decoded synchronously so the initial paint is
    available immediately when the SPA's first /set lands. Subsequent
    frames decode on demand via the daemon thread.

    Decode RAM cost: ~window_size × n_splats × 28 bytes (xyz+quat) plus
    the small static block. For 200k splats × 32 frames that's ~180 MB
    instead of the prior ~2 GB.
    """
    ring = SplatRing(gsq_path)
    cell = make_static_cell(ring, viser_k=_VISER_K)
    # Eager frame-0 decode so the render loop has something to push on
    # the very first tick — the no-skip render contract requires frame 0
    # to be ready before any later frame can advance.
    ring.decode_blocking(0)
    # Hint the decoder to start pre-rolling frames 1..N so the SPA's
    # autoplay (which advances 1→2→3 immediately) doesn't stutter.
    ring.advance(0)
    return cell


def _cell_n_frames(cell: dict) -> int:
    """Return the frame count for any cell shape.

    Supports:
      - ring-backed cells (sliding-window decode) → ring.n_frames
      - legacy / streaming / model cells → frames.shape[0]
      - error stubs (no frames at all) → 0
    """
    ring = cell.get("ring") if isinstance(cell, dict) else None
    if ring is not None:
        return int(ring.n_frames)
    frames = cell.get("frames") if isinstance(cell, dict) else None
    if frames is None:
        return 0
    try:
        return int(frames.shape[0])
    except Exception:
        return 0


def _cell_get_xyz_quat(cell: dict, frame_idx: int) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (xyz, quat) for ``frame_idx`` or ``None`` if not yet decoded.

    Ring-backed cells go through SplatRing.get_frame, which returns None
    when the requested frame is not in the decoded window. Legacy cells
    return the slice directly (always available).
    """
    ring = cell.get("ring") if isinstance(cell, dict) else None
    if ring is not None:
        return ring.get_frame(frame_idx)
    frames = cell.get("frames")
    quats = cell.get("quats")
    if frames is None or quats is None:
        return None
    if frame_idx < 0 or frame_idx >= int(frames.shape[0]):
        return None
    return np.asarray(frames[frame_idx]), np.asarray(quats[frame_idx])


def _close_cell(cell: dict) -> None:
    """Release any background resources held by a cell.

    Currently only relevant to ring-backed cells (they own a daemon
    decoder thread). Called from the LRU eviction path and from
    /clear when the cell is dropped.
    """
    if not isinstance(cell, dict):
        return
    ring = cell.get("ring")
    if ring is not None:
        try:
            ring.close()
        except Exception:
            pass


def decide_next_idx_and_push(
    data: dict,
    desired: int,
    pushed: int,
    scrub_pending: bool,
) -> tuple[int | None, bool, bool]:
    """Strict-sequential next-frame chooser (the no-skip invariant).

    Pure function over (cell data, SPA state). The render loop calls
    this every tick to decide what to push next. Module-scope so it
    can be tested without spinning up viser/FastAPI.

    Returns ``(next_idx, push_now, clear_scrub)``:
      - ``next_idx``: frame the render loop should attempt to push (or
        None to hold the current frame).
      - ``push_now``: True iff that frame's xyz/quat data is already
        decoded and ready. False means a stutter (decoder hasn't
        caught up — render loop holds).
      - ``clear_scrub``: True iff this tick should clear
        ``state["scrub_pending"]`` (the scrub successfully landed).

    Branches:
      - **Scrub jump**: exempt from no-skip; try to land ``desired``
        directly. Hold if not yet decoded.
      - **Initial paint** (``pushed < 0``): wait for frame 0; render
        loop must paint frame 0 before any later advance.
      - **End of sequence**: hold (the SPA's playback driver bounces
        ``desired`` back to 0 if loop is on; render loop has no opinion).
      - **Continuous playback**: advance to ``pushed + 1`` ONLY if
        decoded. Otherwise stutter.
      - **Paused** (``desired <= pushed``): hold.
    """
    n = _cell_n_frames(data)
    ring = data.get("ring") if isinstance(data, dict) else None
    if n <= 0:
        return None, False, False

    if scrub_pending:
        # Exempt from no-skip. Try to land `desired` directly.
        if ring is not None:
            # Best-effort poke (idempotent if already requested).
            ring.request_window(desired)
        pair_ready = _cell_get_xyz_quat(data, desired) is not None
        if pair_ready:
            return desired, True, True
        # Hold pushed_frame until ring catches up. Return ``desired``
        # so the render-loop diagnostics know which frame we're waiting
        # for; ``push_now=False`` keeps the held frame visible.
        return desired, False, False

    if pushed is None or pushed < 0:
        # Initial paint: insist on frame 0, even if SPA already
        # advanced past it (the splat node was just created at 0
        # by _rebuild_scene_node; this confirms it).
        target = 0
        pair_ready = _cell_get_xyz_quat(data, target) is not None
        return (target, pair_ready, False)

    # Normal advance: the loop walks pushed → pushed+1 only when
    # SPA's desired has moved past pushed.
    if desired <= pushed:
        # SPA paused or hasn't crossed the boundary. Nothing to do.
        return None, False, False

    next_idx = pushed + 1
    if next_idx >= n:
        # Past the end. The render loop has no opinion on looping —
        # the SPA's playback driver should bounce `desired` back to
        # 0 if loop is on. From the render loop's view, hold.
        return None, False, False

    pair_ready = _cell_get_xyz_quat(data, next_idx) is not None
    if pair_ready:
        return next_idx, True, False
    # Decoder hasn't caught up: STUTTER. We return ``next_idx`` so the
    # render loop can post a decode request for the frame we're WAITING
    # FOR (helps drain the no-skip backlog). ``push_now=False`` means
    # the loop holds the current frame; the SPA sees its scrub bar
    # frozen on ``pushed_frame`` until the ring catches up.
    return next_idx, False, False


def _build_gsq_cell_dict(xyz: np.ndarray, quat: np.ndarray, rgb_f16: np.ndarray,
                         opacity_u8: np.ndarray, scales_f16: np.ndarray,
                         bbox_min: np.ndarray, bbox_max: np.ndarray,
                         n_loaded: int) -> dict:
    """Assemble the v2 cell dict from already-decoded arrays.

    n_loaded ≤ xyz.shape[0]; n_frames in the dict reflects what's actually
    valid so the render loop won't index into uninitialized rows. bbox
    derives from the global header bbox (not just loaded frames) so the
    grid + camera don't jitter as more frames stream in.
    """
    K2 = _VISER_K * _VISER_K
    bbox_lo = (bbox_min * _VISER_K).astype(np.float32)
    bbox_hi = (bbox_max * _VISER_K).astype(np.float32)
    # Render loop wants `frames.shape[0]` == n_valid frames. Slice the
    # backing array (no copy) so growing n_loaded grows the visible
    # frame range without reallocating.
    scales_f32 = scales_f16.astype(np.float32)
    return {
        "version": 2,
        "frames": xyz[:n_loaded],                         # (n_loaded,N,3)
        "quats": quat[:n_loaded],                         # (n_loaded,N,4)
        "scales_sq": (scales_f32 * scales_f32) * K2,
        "rgb": rgb_f16.astype(np.float32),
        "opacity": (opacity_u8.astype(np.float32) / 255.0).reshape(-1, 1),
        "bbox_lo": bbox_lo,
        "bbox_hi": bbox_hi,
        "_streaming": {
            "xyz_backing": xyz, "quat_backing": quat,
            "n_total": xyz.shape[0], "n_loaded": n_loaded,
        },
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


def _cov_for_frame(data: dict, frame_idx: int,
                   quat_override: np.ndarray | None = None) -> np.ndarray:
    """Per-frame Σᵢ = Rᵢ · diag(scales²) · Rᵢᵀ for v2, or just the static
    cov for v1. Returns a (n, 3, 3) float32 array suitable for assignment
    to viser's `splat.covariances`.

    ``quat_override`` lets the render loop pass in a quaternion already
    pulled from a ring (avoids a double lookup). When None, falls back
    to ``data["quats"][frame_idx]`` (legacy/model path).
    """
    if data["version"] == 1:
        return np.ascontiguousarray(data["cov"])
    if quat_override is not None:
        q = np.asarray(quat_override)
    else:
        q = np.asarray(data["quats"][frame_idx])           # (n, 4)
    R = _quats_to_R(q)                                     # (n, 3, 3)
    S2 = data["scales_sq"]                                 # (n, 3)
    # Scale each column of R by S² (because Σ = R · diag(s²) · Rᵀ ⇒
    # R · diag(s²) writes the diagonal as a per-column multiplier).
    # The (n, 1, 3) broadcast over the last axis is the right shape.
    R_S2 = R * S2[:, None, :]                              # (n, 3, 3)
    # matmul dispatches to OpenBLAS batched gemm — ~1.5× faster than einsum
    # for this (n,3,3) shape, and already multi-threaded across cores.
    cov = np.matmul(R_S2, R.transpose(0, 2, 1)).astype(np.float32)
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
    p.add_argument(
        "--cache-dir", dest="cache_dir", required=True,
        help="Directory containing per-sequence .gsq cache files",
    )
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

    cache_root = Path(args.cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    # Lazy boot: just enumerate available .gsq files; do not decode.
    # Each .gsq decode is ~1-3s + hundreds of MB of dequantized float32,
    # so eager-loading 4+ cells used to take 10-30s and 3 GB of RAM at
    # boot — for cells the user might never click. resolve_cell_lazily
    # loads them on first /set with a "parsing" phase pill so the SPA
    # shows progress; subsequent clicks are instant from the cells dict.
    available = sorted(cache_root.glob("*.gsq"))
    print(f"boot: {len(available)} .gsq cells available in {cache_root} (loaded on demand)")
    for path in available:
        print(f"  available: sequence:{path.stem}  ({path.stat().st_size / 1e6:.0f} MB)")
    # Bounded LRU around decoded cells. The eviction `pinned` callback
    # late-binds to state["cell"], which is created a few hundred lines
    # below — Python closures resolve names at call time, so by the time
    # the first insert happens (inside a FastAPI handler after main()
    # has fully built `state`) the lookup succeeds. The NameError guard
    # below handles the (impossible-today) case of an eviction firing
    # during the brief window between cells construction and state
    # construction.
    _max_cached_cells_str = _os.environ.get("GSFLUENT_MAX_CACHED_CELLS", "5")
    try:
        _max_cached_cells = int(_max_cached_cells_str)
    except ValueError:
        print(f"  warn: GSFLUENT_MAX_CACHED_CELLS={_max_cached_cells_str!r} "
              "is not an int; using default 5")
        _max_cached_cells = 5

    def _active_cell_name() -> str | None:
        try:
            return state["cell"]  # type: ignore[name-defined]
        except NameError:
            return None

    cells: _CellLRU = _CellLRU(
        max_size=_max_cached_cells,
        pinned=_active_cell_name,
        emit=_emit_event,
    )

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
          2. sequence:<seqName> → look for <seqName>.gsq under cache_root
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
            gsq = cache_root / f"{seq_name}.gsq"
            if not gsq.is_file():
                return False, "not_found"
            _set_loading(name, "parsing")
            try:
                cells[name] = load_cell_gsq(gsq)
                print(f"  loaded sequence cell {name} from {gsq}")
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

    # Bootstrap scene helpers (grid, gizmo, initial camera). Under
    # lazy-decode boot, `cells` is empty at this point, so derive a
    # bbox cheaply from the first available .gsq's 80-byte header (no
    # frame decompression). Falls back to a neutral bbox if nothing is
    # on disk yet. We do NOT auto-load any splat — the splat node is
    # only added when the user picks a cell via /set.
    if cells:
        cur = next(iter(cells.values()))
    elif available:
        with open(available[0], "rb") as _f:
            _head = _f.read(80)
        if _head[:4] != b"GSQ1":
            raise SystemExit(f"corrupt .gsq header at {available[0]}")
        # _VISER_K matches _build_gsq_cell_dict so units agree once the
        # real cell loads via resolve_cell_lazily on the first /set.
        _bbox_min = np.frombuffer(_head[20:32], dtype=np.float32)
        _bbox_max = np.frombuffer(_head[32:44], dtype=np.float32)
        cur = {
            "bbox_lo": (_bbox_min * _VISER_K).astype(np.float32),
            "bbox_hi": (_bbox_max * _VISER_K).astype(np.float32),
        }
    else:
        cur = {
            "bbox_lo": np.array([-10.0, -10.0, -2.0], dtype=np.float32),
            "bbox_hi": np.array([10.0, 10.0,  8.0], dtype=np.float32),
        }
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
        # Set when /set receives a frame request that's > 1 away from
        # `pushed_frame` — treated as a scrub jump and the render loop
        # exempts it from the no-skip invariant. The loop clears the
        # flag once the target frame is decoded + pushed.
        "scrub_pending": False,
    }
    lock = threading.Lock()

    def _rebuild_scene_node():
        """Remove + re-add the splat node for the current cell.
        Called on cell-swap. ~10ms on cluster_6_15-class data.

        For ring-backed cells, this attempts a synchronous decode of the
        active frame so the first paint is correct. If the decode fails
        (e.g. ring not yet warmed) the node is created with frame 0 —
        whichever frame the decoder has ready first will be pushed on
        the next render tick via the no-skip advance path.
        """
        nonlocal splat
        cur_c = cells[state["cell"]]
        frame_idx = state["frame"]
        ring = cur_c.get("ring") if isinstance(cur_c, dict) else None
        if ring is not None:
            # Ring path: ensure the requested frame is decoded. If we
            # can't get it (e.g. timeout), fall back to frame 0 so the
            # node has something to display.
            try:
                xyz, quat = ring.decode_blocking(frame_idx)
            except Exception:
                try:
                    xyz, quat = ring.decode_blocking(0)
                    frame_idx = 0
                except Exception:
                    return  # nothing to render; render loop will retry
        else:
            pair = _cell_get_xyz_quat(cur_c, frame_idx)
            if pair is None:
                return
            xyz, quat = pair
        centers = np.ascontiguousarray(np.asarray(xyz) * _VISER_K)
        if splat is not None:
            try:
                splat.remove()
            except Exception:
                pass
            splat = None
        splat = server.scene.add_gaussian_splats(
            "splat",
            centers=centers,
            covariances=_cov_for_frame(cur_c, frame_idx, quat_override=quat),
            rgbs=np.ascontiguousarray(cur_c["rgb"]),
            opacities=np.ascontiguousarray(cur_c["opacity"]),
            position=(0.0, 0.0, -float(cur_c["bbox_lo"][2])),
        )
        # The rebuild just populated the node with the current frame's
        # data, so the render loop doesn't need to push again this tick.
        state["pushed_cell"] = state["cell"]
        state["pushed_frame"] = frame_idx

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
        elif body.cell is not None:
            # Fast path (cell already in cells). Clear any stale loading
            # state from a prior failed /set so the SPA doesn't render
            # an error pill for a click that's no longer the active one.
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
                    # the render loop's frame lookup would raise IndexError
                    # and silently kill the render thread. Clamp.
                    n_new = _cell_n_frames(cells[state["cell"]])
                    # Apply the requested frame BEFORE rebuilding, so the
                    # scene node's first paint is the requested frame — not
                    # the stale frame index left over from the previous
                    # cell. Without this, switching to a new sequence while
                    # the previous one was parked at a late frame paints the
                    # new cell at that late index first (e.g. a demolition's
                    # fully-scattered frame 140, which reads as "almost
                    # empty") before the scrub path recovers it.
                    if body.frame is not None:
                        state["frame"] = max(0, min(int(body.frame), n_new - 1))
                    elif state["frame"] >= n_new:
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
                    n = _cell_n_frames(cells[cur])
                    requested = max(0, min(int(body.frame), n - 1))
                    # Detect a scrub: large jump from the currently
                    # rendered frame. The render loop honors this exempt
                    # path via state["scrub_pending"] — see the no-skip
                    # invariant inside the render loop.
                    pushed = state.get("pushed_frame", -1)
                    if pushed is not None and pushed >= 0 and abs(requested - pushed) > 1:
                        state["scrub_pending"] = True
                        cell_obj = cells[cur]
                        ring = cell_obj.get("ring") if isinstance(cell_obj, dict) else None
                        if ring is not None:
                            ring.request_window(requested)
                    state["frame"] = requested
                else:
                    state["frame"] = max(0, int(body.frame))
            return {"ok": True, "cell": state["cell"],
                    "frame": state["frame"],
                    "pushed_frame": state.get("pushed_frame", -1)}

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
                    "pushed_frame": -1,
                    "n_frames": 0,
                    "cells": list(cells),
                    "bbox": None,
                    "loading": loading,
                }
            cur_c = cells[cell]
            # `pushed_frame` is the frame that's actually rendered on
            # viser — distinct from `frame`, which is the SPA's desired
            # playback cursor. The SPA displays pushed_frame on the
            # scrub bar so the displayed index never leads the splats
            # (no-skip invariant: render loop only advances by 1 per
            # tick, holds when next decode isn't ready).
            return {
                "cell": cell,
                "frame": state["frame"],
                "pushed_frame": state.get("pushed_frame", -1),
                "n_frames": _cell_n_frames(cur_c),
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

    def _swap_to_ring_cell(cell_key: str, gsq_path: Path) -> None:
        """Replace the streaming-array cell at ``cell_key`` with a SplatRing.

        Called once the .gsq file is complete on disk (post-rename from
        .partial). The legacy frames/quats arrays drop out of RAM as
        soon as the dict reference is replaced — Python's refcount frees
        the large float32 buffers immediately on assignment.

        If the cell isn't the currently-active one, we still swap so the
        LRU stores the small ring-backed shell; next time the user
        clicks the cell, render loop's first tick finds a ring and the
        no-skip advance path takes over.

        On swap failure (rare — only happens if the file is corrupt
        post-rename), the legacy cell is left in place. The user sees
        normal playback at the old higher RAM cost; nothing breaks.
        """
        try:
            new_ring = SplatRing(gsq_path)
            new_ring.decode_blocking(0)
            new_cell = make_static_cell(new_ring, viser_k=_VISER_K)
        except Exception as e:
            print(f"  swap-to-ring failed for {cell_key}: {e}; keeping legacy cell")
            return
        with lock:
            old = cells.get(cell_key)
            cells[cell_key] = new_cell
            if state["cell"] == cell_key:
                state["scene_dirty"] = True
                state["pushed_frame"] = -1
        if old is not None:
            _close_cell(old)

    def _sync_cell_gsq_streaming_with_prefix(
        *,
        name: str,
        dest: Path,
        partial: Path,
        response,                 # httpx.Response in stream mode
        prefix: bytes,
        cell_key: str,
    ) -> dict:
        """Decode a .gsq stream that resumed mid-download.

        Pre-seeds the decode buffer with `prefix` (the bytes already on
        disk from the prior interrupted run), then continues from the
        206 response body. The static block + frame index live near the
        head of the file, so a resumed download where the offset is
        > header_size still works because the decoder operates on a
        single concatenated buffer.

        Returns the same dict shape as a fresh download:
            {ok, cell, added, cached?, bytes, n_frames}
        """
        import struct as _struct
        import zstandard as _zstd

        buf = bytearray(prefix)
        pf = open(partial, "ab")
        header_parsed = None
        static_decoded = False
        rgb_f16 = opacity_u8 = scales_f16 = None
        xyz_backing = quat_backing = None
        n_loaded = 0
        bbox_min = bbox_max = span = None

        def commit_cell():
            cell = _build_gsq_cell_dict(
                xyz_backing, quat_backing, rgb_f16, opacity_u8,
                scales_f16, bbox_min, bbox_max, n_loaded=n_loaded,
            )
            with lock:
                old = cells.get(cell_key)
                cells[cell_key] = cell
                if state["cell"] == cell_key:
                    state["scene_dirty"] = True
                    state["pushed_frame"] = -1
            # Close any prior ring-backed cell. Same dict produced by a
            # prior streaming commit would be a legacy cell with no ring,
            # so _close_cell is a no-op in the common case.
            if old is not None and old is not cell:
                _close_cell(old)

        # Decode whatever is already in the prefix BEFORE any new bytes
        # arrive. This handles the case where the prior run had decoded
        # the static block + several frames but never flipped partial ->
        # dest. The same per-chunk logic below is just looped once with
        # no new bytes.
        def _try_advance():
            nonlocal header_parsed, static_decoded, rgb_f16, opacity_u8
            nonlocal scales_f16, xyz_backing, quat_backing, n_loaded
            nonlocal bbox_min, bbox_max, span

            if header_parsed is None and len(buf) >= 80:
                n_frames_peek = _struct.unpack_from("<I", bytes(buf[:80]), 12)[0]
                need = 80 + n_frames_peek * 16
                if len(buf) >= need:
                    header_parsed = parse_gsq_header(bytes(buf[:need]))
                    bbox_min = header_parsed["bbox_min"]
                    bbox_max = header_parsed["bbox_max"]
                    span = (bbox_max - bbox_min).astype(np.float32)
                    span[span == 0] = 1.0
                    xyz_backing = np.zeros(
                        (header_parsed["n_frames"], header_parsed["n_splats"], 3),
                        dtype=np.float32,
                    )
                    quat_backing = np.zeros(
                        (header_parsed["n_frames"], header_parsed["n_splats"], 4),
                        dtype=np.float32,
                    )
                    quat_backing[..., 0] = 1.0
                    _set_loading(cell_key, "streaming")

            if (header_parsed is not None and not static_decoded
                    and len(buf) >= header_parsed["static_offset"] + header_parsed["static_size"]):
                s_off = header_parsed["static_offset"]
                s_sz = header_parsed["static_size"]
                n_sp = header_parsed["n_splats"]
                blob = _zstd.ZstdDecompressor().decompress(
                    bytes(buf[s_off : s_off + s_sz])
                )
                rgb_bytes = n_sp * 3 * 2
                rgb_f16 = np.frombuffer(blob[:rgb_bytes], dtype=np.float16).reshape(n_sp, 3).copy()
                opacity_u8 = np.frombuffer(blob[rgb_bytes:rgb_bytes + n_sp], dtype=np.uint8).copy()
                scales_f16 = np.frombuffer(
                    blob[rgb_bytes + n_sp : rgb_bytes + n_sp + n_sp * 3 * 2],
                    dtype=np.float16,
                ).reshape(n_sp, 3).copy()
                static_decoded = True

            if static_decoded:
                n_sp = header_parsed["n_splats"]
                n_total = header_parsed["n_frames"]
                while n_loaded < n_total:
                    f_off, f_sz = header_parsed["frame_index"][n_loaded]
                    if len(buf) < f_off + f_sz:
                        break
                    xyz, quat = _gsq_dequantize_frame(
                        bytes(buf[f_off : f_off + f_sz]),
                        n_sp, bbox_min, span,
                    )
                    xyz_backing[n_loaded] = xyz
                    quat_backing[n_loaded] = quat
                    n_loaded += 1
                if n_loaded > 0:
                    commit_cell()

        # Decode whatever the prefix already covers.
        _try_advance()

        try:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                buf.extend(chunk)
                pf.write(chunk)
                _try_advance()
            pf.close()
        except Exception:
            pf.close()
            raise

        partial.replace(dest)

        if header_parsed is None or not static_decoded or n_loaded == 0:
            _set_loading(cell_key, "error", "stream_failed")
            return {"ok": False, "error":
                    f"incomplete .gsq after resume: parsed_header="
                    f"{header_parsed is not None}, static={static_decoded}, "
                    f"frames={n_loaded}"}

        # Stream complete + on-disk: replace the in-RAM frames/quats
        # arrays with a SplatRing pointing at the final .gsq. Drops
        # ~1-2 GB of dequantized float32 in exchange for a small
        # background decoder thread.
        _swap_to_ring_cell(cell_key, dest)
        _set_loading(None, None)
        return {
            "ok": True, "cell": name, "added": True, "resumed": True,
            "bytes": dest.stat().st_size, "n_frames": n_loaded,
        }

    def _sync_cell_gsq_streaming(name: str, url: str, dest: Path, partial: Path) -> dict:
        """Streaming .gsq download + incremental decode, with cache-hit + resume.

        Three entry paths, taken in order:

        1. HEAD probe (if dest exists). If the server's ETag matches our
           _local_etag(dest), or content-length matches dest.stat().st_size
           (back-compat for pre-Phase-5 servers that don't emit ETag),
           skip the body entirely and load the cell from disk. Emits
           cell.cache.hit.

        2. Range resume (if .partial exists). Send Range: bytes=<n>-,
           treat 206 as resume (append, decode-as-arrives accounting for
           the offset), treat 200 as "server ignored Range" (unlink
           .partial and fall through to a fresh download). Emits
           cell.cache.resuming.

        3. Fresh streaming download (the existing path). Reads the
           request body once. The first chunk(s) supply the header +
           frame index — we know the static block offset and the
           per-frame byte ranges. Each subsequent chunk extends a
           buffer; whenever we have enough bytes for the static block
           and then each next frame, we decode and grow the cell.

           cells[name] appears the moment frame 0 is decoded. n_loaded
           grows monotonically until the whole file lands. /state polls
           see n_frames = n_loaded, so the SPA can scrub right away.
        """
        import zstandard as _zstd

        cell_key = name if ":" in name else f"sequence:{name}"

        # --- Path 1: cache hit on HEAD probe ---------------------------------
        if dest.is_file():
            try:
                head = httpx.head(url, timeout=10.0,
                                  follow_redirects=True, trust_env=False)
            except Exception as e:
                # Network error on HEAD is non-fatal — fall through to a
                # fresh download. The body request below will fail with
                # the same error and the user sees the same surface.
                print(f"  cache HEAD failed for {name}: {e}; falling through to download")
                head = None
            if head is not None and head.status_code == 200:
                remote_etag = head.headers.get("etag")
                local_etag_val = None
                try:
                    local_etag_val = _local_etag(dest)
                except FileNotFoundError:
                    pass  # raced with a delete; just download

                etag_match = (
                    remote_etag is not None
                    and local_etag_val is not None
                    and remote_etag == local_etag_val
                )
                size_match = False
                if not etag_match:
                    # Back-compat: server may not emit ETag yet (older
                    # deployments). Compare content-length instead.
                    try:
                        remote_size = int(head.headers.get("content-length", "-1"))
                        size_match = remote_size >= 0 and remote_size == dest.stat().st_size
                    except (ValueError, OSError):
                        size_match = False

                if etag_match or size_match:
                    source = "etag" if etag_match else "size"
                    try:
                        cell = load_cell_gsq(dest)
                    except Exception as e:
                        # Local file is current per the server, but our
                        # decoder choked. Could be a stale Phase 1/2
                        # format we no longer support. Fall through to a
                        # fresh download with a structured note.
                        print(f"  cache hit decode failed for {name}: {e}; re-downloading")
                    else:
                        with lock:
                            old_cached = cells.get(cell_key)
                            cells[cell_key] = cell
                            if state["cell"] == cell_key:
                                state["scene_dirty"] = True
                                state["pushed_frame"] = -1
                        # Release any prior ring/file handles outside
                        # the lock so a slow teardown can't stall /state
                        # polls. Idempotent on non-ring cells.
                        if old_cached is not None and old_cached is not cell:
                            _close_cell(old_cached)
                        _set_loading(None, None)
                        _emit_event(
                            "cell.cache.hit",
                            cell=name,
                            source=source,
                            path=str(dest),
                            bytes=dest.stat().st_size,
                        )
                        return {
                            "ok": True, "cell": name, "added": False,
                            "cached": True, "source": source,
                            "bytes": dest.stat().st_size,
                            "n_frames": int(cell.get("n_frames", 0)),
                        }

        # --- Path 2: resume from .partial -----------------------------------
        # An interrupted prior download leaves <dest>.partial on disk. We
        # send Range: bytes=<n>- where n = partial size. If the server
        # honors it (206 Partial Content), we append to the partial,
        # decode against the file-relative byte offsets (the parser uses
        # absolute offsets from the .gsq header, so we must rebuild the
        # full buffer from the on-disk prefix + the streamed suffix). If
        # the server returns 200 (Range ignored), we unlink the partial
        # and let Path 3 (fresh download) re-fetch from byte 0.
        resume_offset = 0
        prefix_bytes: bytes | None = None
        if partial.is_file():
            try:
                resume_offset = partial.stat().st_size
            except OSError:
                resume_offset = 0
            if resume_offset > 0:
                # Best-effort: only resume when the prefix is non-empty.
                # Zero-byte partials happen on rare crash modes; treat as
                # fresh.
                _emit_event(
                    "cell.cache.resuming",
                    cell=name,
                    resume_offset=resume_offset,
                )
                try:
                    headers = {"Range": f"bytes={resume_offset}-"}
                    with httpx.stream("GET", url, headers=headers,
                                      timeout=600.0, follow_redirects=True,
                                      trust_env=False) as r:
                        if r.status_code == 206:
                            # Server honored Range. Re-open the partial
                            # for append + read prefix into memory once
                            # so the existing decoder can index by
                            # absolute offset.
                            prefix_bytes = partial.read_bytes()
                            try:
                                ok = _sync_cell_gsq_streaming_with_prefix(
                                    name=name, dest=dest, partial=partial,
                                    response=r, prefix=prefix_bytes,
                                    cell_key=cell_key,
                                )
                            except Exception as e:
                                partial.unlink(missing_ok=True)
                                _set_loading(cell_key, "error", "resume_failed")
                                return {"ok": False, "error": f"resume failed: {e}"}
                            return ok
                        elif r.status_code == 200:
                            # Server returned full body. Discard the
                            # partial and fall through to fresh-download
                            # path below.
                            partial.unlink(missing_ok=True)
                            resume_offset = 0
                            print(f"  server ignored Range for {name}; restarting at byte 0")
                        else:
                            partial.unlink(missing_ok=True)
                            _set_loading(cell_key, "error", "resume_failed")
                            return {"ok": False, "error":
                                    f"resume HTTP {r.status_code}"}
                except Exception as e:
                    # Network error during resume. Drop partial and try
                    # a fresh download from byte 0.
                    print(f"  resume network error for {name}: {e}; restarting at byte 0")
                    partial.unlink(missing_ok=True)
                    resume_offset = 0

        # --- Path 3: fresh download (existing path) -------------------------
        try:
            with httpx.stream("GET", url, timeout=600.0,
                              follow_redirects=True, trust_env=False) as r:
                if r.status_code != 200:
                    return {"ok": False, "error":
                            f"download failed: HTTP {r.status_code}"}

                buf = bytearray()
                pf = open(partial, "wb")
                header_parsed = None      # dict from parse_gsq_header
                static_decoded = False
                rgb_f16 = opacity_u8 = scales_f16 = None
                xyz_backing = quat_backing = None
                n_loaded = 0
                bbox_min = bbox_max = span = None

                # cell_key is computed once at the top of the enclosing
                # function (see Path 1 / Path 2 / Path 3 docstring).
                # Re-binding here was redundant pre-Phase-5 and would
                # shadow the outer name; left as the docstring note.

                def commit_cell():
                    """Publish current state under the lock + nudge render."""
                    cell = _build_gsq_cell_dict(
                        xyz_backing, quat_backing, rgb_f16, opacity_u8,
                        scales_f16, bbox_min, bbox_max, n_loaded=n_loaded,
                    )
                    with lock:
                        old = cells.get(cell_key)
                        cells[cell_key] = cell
                        if state["cell"] == cell_key:
                            state["scene_dirty"] = True
                            state["pushed_frame"] = -1
                    # Release any prior ring (rare — would only happen
                    # if the user re-downloads while a ring is live).
                    if old is not None and old is not cell:
                        _close_cell(old)

                try:
                    for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                        buf.extend(chunk)
                        pf.write(chunk)

                        # Phase 1: header + frame index.
                        if header_parsed is None and len(buf) >= 80:
                            import struct as _struct
                            n_frames_peek = _struct.unpack_from("<I", bytes(buf[:80]), 12)[0]
                            need = 80 + n_frames_peek * 16
                            if len(buf) >= need:
                                header_parsed = parse_gsq_header(bytes(buf[:need]))
                                bbox_min = header_parsed["bbox_min"]
                                bbox_max = header_parsed["bbox_max"]
                                span = (bbox_max - bbox_min).astype(np.float32)
                                span[span == 0] = 1.0
                                xyz_backing = np.zeros(
                                    (header_parsed["n_frames"], header_parsed["n_splats"], 3),
                                    dtype=np.float32,
                                )
                                quat_backing = np.zeros(
                                    (header_parsed["n_frames"], header_parsed["n_splats"], 4),
                                    dtype=np.float32,
                                )
                                quat_backing[..., 0] = 1.0  # identity rotation default
                                _set_loading(cell_key, "streaming")

                        # Phase 2: static block.
                        if (header_parsed is not None and not static_decoded
                                and len(buf) >= header_parsed["static_offset"] + header_parsed["static_size"]):
                            s_off = header_parsed["static_offset"]
                            s_sz = header_parsed["static_size"]
                            n_sp = header_parsed["n_splats"]
                            blob = _zstd.ZstdDecompressor().decompress(bytes(buf[s_off : s_off + s_sz]))
                            rgb_bytes = n_sp * 3 * 2
                            rgb_f16 = np.frombuffer(blob[:rgb_bytes], dtype=np.float16).reshape(n_sp, 3).copy()
                            opacity_u8 = np.frombuffer(blob[rgb_bytes:rgb_bytes + n_sp], dtype=np.uint8).copy()
                            scales_f16 = np.frombuffer(
                                blob[rgb_bytes + n_sp : rgb_bytes + n_sp + n_sp * 3 * 2],
                                dtype=np.float16,
                            ).reshape(n_sp, 3).copy()
                            static_decoded = True

                        # Phase 3: decode as many subsequent frames as the buffer covers.
                        if static_decoded:
                            n_sp = header_parsed["n_splats"]
                            n_total = header_parsed["n_frames"]
                            while n_loaded < n_total:
                                f_off, f_sz = header_parsed["frame_index"][n_loaded]
                                if len(buf) < f_off + f_sz:
                                    break  # not enough bytes yet
                                xyz, quat = _gsq_dequantize_frame(
                                    bytes(buf[f_off : f_off + f_sz]),
                                    n_sp, bbox_min, span,
                                )
                                xyz_backing[n_loaded] = xyz
                                quat_backing[n_loaded] = quat
                                n_loaded += 1
                            # Publish whenever we made progress AND we have
                            # at least frame 0 — gates the first visible
                            # render on the first decoded frame, not the
                            # first arriving byte.
                            if n_loaded > 0:
                                commit_cell()

                    pf.close()
                except Exception as e:
                    pf.close()
                    partial.unlink(missing_ok=True)
                    _set_loading(cell_key, "error", "stream_failed")
                    return {"ok": False, "error": f"stream failed: {e}"}

            partial.replace(dest)

            if header_parsed is None or not static_decoded or n_loaded == 0:
                _set_loading(cell_key, "error", "stream_failed")
                return {"ok": False, "error":
                        f"incomplete .gsq: parsed_header={header_parsed is not None}, "
                        f"static={static_decoded}, frames={n_loaded}"}

            # Stream complete + on-disk: swap from the in-RAM streaming
            # arrays to a SplatRing reading from `dest`. This is where the
            # sliding-window RAM win lands — the legacy ~2 GB of decoded
            # float32 arrays go away and the cell drops to a ring +
            # static block + decoder thread (~200 MB worst case).
            _swap_to_ring_cell(cell_key, dest)
            _set_loading(None, None)

            return {"ok": True, "cell": name, "added": True,
                    "bytes": dest.stat().st_size,
                    "n_frames": n_loaded}
        except Exception as e:
            partial.unlink(missing_ok=True)
            return {"ok": False, "error": f"stream failed: {e}"}

    @api.post("/sync_cell")
    def sync_cell(name: str, url: str) -> dict:
        """Load cell `name` from `url`, with LOD when a base layer exists.

        Strategy (see _lod_decision):
          - full already cached  -> load full directly (existing cache-hit path).
          - base layer available -> stream base (plays), then download the full
            file silently and swap to it at the current playhead.
          - no base layer        -> stream full directly.
        The SPA makes the same single call; LOD is transparent.
        """
        if not _SAFE_NAME.match(name):
            return {"ok": False, "error": f"invalid cell name: {name!r}"}
        cell_key = f"sequence:{name}"
        full_dest = (cache_root / f"{name}.gsq").resolve()
        base_dest = (cache_root / f"{name}.base.gsq").resolve()
        for d in (full_dest, base_dest):
            try:
                d.relative_to(cache_root.resolve())
            except ValueError:
                return {"ok": False, "error": f"cell path escapes cache_dir: {name!r}"}
        full_partial = full_dest.with_suffix(".gsq.partial")
        base_partial = base_dest.with_suffix(".base.gsq.partial")

        # Is the local full file current vs the server?
        full_is_current = False
        if full_dest.is_file():
            try:
                head = httpx.head(url, timeout=10.0, follow_redirects=True, trust_env=False)
                if head.status_code == 200:
                    remote_etag = head.headers.get("etag")
                    same_etag = remote_etag is not None and remote_etag == _local_etag(full_dest)
                    same_size = str(full_dest.stat().st_size) == head.headers.get("content-length", "")
                    full_is_current = bool(same_etag or same_size)
            except Exception:
                full_is_current = False

        base_url = _base_url_from_full(url)
        base_status = None
        if not full_is_current:
            try:
                base_status = httpx.head(base_url, timeout=10.0,
                                         follow_redirects=True, trust_env=False).status_code
            except Exception:
                base_status = None

        decision = _lod_decision(full_is_current=full_is_current, base_status=base_status)

        if decision in ("full-direct", "full-only"):
            return _sync_cell_gsq_streaming(cell_key, url, full_dest, full_partial)

        # two-tier: base now, full+swap after.
        _set_loading(cell_key, "streaming-base")
        base_res = _sync_cell_gsq_streaming(cell_key, base_url, base_dest, base_partial)
        if not base_res.get("ok"):
            # base failed -> fall straight to full streaming.
            return _sync_cell_gsq_streaming(cell_key, url, full_dest, full_partial)
        try:
            _set_loading(cell_key, "streaming-full")
            _download_gsq_to_disk(url, full_dest)
        except Exception as e:
            # Full failed after base -> keep base playing, surface the error.
            _set_loading(cell_key, "error", "full_stream_failed")
            return {"ok": True, "cell": name, "lod": "base-only",
                    "n_frames": int(base_res.get("n_frames", 0)),
                    "error": f"full layer failed: {e}"}
        _swap_to_ring_cell(cell_key, full_dest)
        _set_loading(None, None)
        return {"ok": True, "cell": name, "lod": "full",
                "bytes": full_dest.stat().st_size,
                "n_frames": int(base_res.get("n_frames", 0))}

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
        gsq_path = (cache_root / f"{cell}.gsq").resolve()
        try:
            gsq_path.relative_to(cache_root.resolve())
        except ValueError:
            return {"ok": False, "error": f"cell path escapes cache_dir: {cell!r}"}
        if not gsq_path.is_file():
            return {"ok": False, "error": f"no .gsq at {gsq_path}"}
        try:
            new_data = load_cell_gsq(gsq_path)
        except Exception as e:
            return {"ok": False, "error": f"load failed: {e}"}
        with lock:
            was_new = cell not in cells
            old = cells.get(cell)
            cells[cell] = new_data
            if state["cell"] == cell:
                # Force a full re-push: cells[cell] is a fresh dict, so
                # the static attrs (cov/rgb/opacity) and the bbox-derived
                # grid + camera all need to be re-asserted.
                state["scene_dirty"] = True
                state["pushed_frame"] = -1
        # Release the prior cell's decoder thread + file handles. Done
        # outside the lock so a slow ring teardown can't stall /state polls.
        if old is not None and old is not new_data:
            _close_cell(old)
        return {"ok": True, "cell": cell, "added": was_new,
                "n_frames": _cell_n_frames(new_data)}

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
        _tick_start = time.perf_counter()
        with lock:
            cell = state["cell"]
            desired = state["frame"]
            pushed = state.get("pushed_frame", -1)
            need_full_swap = cell != state["pushed_cell"]
            need_scene_redo = state["scene_dirty"]
            scrub_pending = bool(state.get("scrub_pending", False))

        # Empty scene path: no cell selected (startup state) means nothing
        # to render. Just spin until /set delivers a cell. 1/30 matches
        # the steady-state tick rate at the bottom of the loop.
        if cell is None:
            time.sleep(1 / 30)
            continue

        # --- pick which frame to push this tick (no-skip invariant) -------
        data = cells[cell]
        if need_full_swap:
            # Cell just swapped — _rebuild_scene_node already pushed the
            # active frame and updated pushed_*. Render loop has nothing
            # extra to do this tick beyond accounting.
            with lock:
                if state["pushed_cell"] != cell:
                    # Defensive: _rebuild_scene_node wasn't called for
                    # this cell (e.g. /set failed). Reset pushed_frame
                    # to -1 so the initial-paint branch handles it next
                    # tick.
                    state["pushed_cell"] = cell
                    state["pushed_frame"] = -1
        else:
            next_idx, push_now, clear_scrub = decide_next_idx_and_push(
                data, desired, pushed, scrub_pending,
            )
            if next_idx is None or not push_now:
                # Stutter: hold current frame. Account it so /state can
                # surface decode pressure.
                ring = data.get("ring") if isinstance(data, dict) else None
                if next_idx is not None and not push_now and ring is not None:
                    # Decoder didn't catch up — request and account.
                    ring.request_frame(next_idx)
                    ring.note_stutter()
                # When scrub_pending and we couldn't land yet, keep the
                # flag set so the next tick keeps trying.
            else:
                # We have a frame to push. Hold the lock for the actual
                # WS attr writes so /set's _rebuild_scene_node (called
                # from a worker thread) can't race in mid-attribute write.
                pair = _cell_get_xyz_quat(data, next_idx)
                if pair is not None:
                    xyz, quat = pair
                    # Compute cov + centers OUTSIDE the lock — these are the
                    # expensive ops (cov is ~40-80ms even after matmul). Holding
                    # the lock during them stalls /set + /state for that long.
                    cov = _cov_for_frame(data, next_idx, quat_override=quat)
                    centers = np.ascontiguousarray(np.asarray(xyz) * _VISER_K)
                    wrote = False
                    with lock:
                        local_splat = splat
                        if local_splat is not None:
                            try:
                                local_splat.covariances = cov
                                local_splat.centers = centers
                                wrote = True
                            except RuntimeError:
                                pass  # handle removed mid-write; retry next tick
                        if wrote:
                            state["pushed_cell"] = cell
                            state["pushed_frame"] = next_idx
                            if clear_scrub:
                                state["scrub_pending"] = False
                    # Hint the decoder thread to keep prefetching ahead
                    # of where we just landed.
                    ring = data.get("ring") if isinstance(data, dict) else None
                    if ring is not None and wrote:
                        ring.advance(next_idx)

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

        # Adaptive pacing: sleep only the remainder of the frame budget so a
        # slow tick doesn't stack the full 33ms on top of its work. The cell
        # dict does not carry an fps_hint, so target 24fps. Floor at 1ms so a
        # slow tick still yields the GIL.
        _target_dt = 1.0 / 24.0
        _elapsed = time.perf_counter() - _tick_start
        time.sleep(max(0.001, _target_dt - _elapsed))

    # Clean shutdown — try to stop viser's WS so the browser sees a
    # proper close frame rather than a TCP reset.
    print("\n>>> viser_headless: stop requested, shutting down…")
    try:
        server.stop()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
