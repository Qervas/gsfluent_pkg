"""Sequences API — list / import / serve frames / delete library sequences.

Both sim-produced sequences (source="sim", written by the runner) and
external imports (source="import", symlinked via library.import_sequence)
live in the same dir at `work/library/sequences/<name>/`. The Outliner
surfaces them in a single tree; this router is the unified backend.

The frame-serving endpoint is intentionally aliased to the existing one
in api/runs.py so the frontend WebSocket bootstrap (which hardcodes
`/api/runs/<name>/frame/0.ply`) keeps working — see `api/runs.py:get_run_frame`.
"""
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import library as lib
from ..core.library import Sequence, import_sequence
from ..protocols.runs import RunManager
from ..server import PKG_ROOT

# Frame-serving handler is shared with /api/runs/{name}/frame/{idx}.ply
# so the two URL shapes return the exact same bytes for the same args.
# Imported at the top (was originally a late-import inside the function
# to avoid circular-import worries — verified no longer needed).
from .runs import _get_run_mgr
from .runs import get_run_frame as _get_run_frame

router = APIRouter(prefix="/api/sequences", tags=["sequences"])

# Where derived caches live. The viser .gsq cache is built by
# `server/tools/pack_splats.py` and consumed by
# `frontend/python/viser_headless.py`. Under the split-topology deployment
# the server holds the canonical copy and the SPA streams it on demand
# through viser_headless's /sync_cell.
_VISER_CACHE = PKG_ROOT / "work" / "cache" / "viser"


def _sequence_dict(seq: Sequence, *, active_names: set[str] | None = None) -> dict:
    """Build the frontend-facing sequence dict.

    Carries everything in `_meta.json` plus `is_broken` (computed from the
    frames symlink state). Frame count is taken from meta when present,
    falling back to a live filesystem count for sim-produced sequences
    that may be growing as we read.

    `active_names` is the set of currently-running sequence names (taken
    from RunManager.list_active()). When this sequence is among them,
    we re-walk the frames dir for live frame counts.
    """
    if active_names is None:
        active_names = set()
    d = seq.meta_dict()
    # Always emit is_broken — frontend reads it to decide whether to show
    # the warning indicator.
    d["is_broken"] = bool(seq.is_broken)
    # `meta_dict()` injects the absolute server filesystem path
    # (e.g. <sequences-root>/foo/) into every payload.
    # The React workbench doesn't consume it; under split-topology the
    # client has no use for the server's local path either. Stripping
    # it keeps the API surface from leaking server directory layout.
    d.pop("path", None)
    # Default-fill the fields the frontend SequenceItem type expects, so
    # legacy sequences without a complete _meta.json still render.
    d.setdefault("source", "unknown")
    d.setdefault("source_path", None)
    d.setdefault("model_ref", None)
    # In-flight sims haven't written their canonical _meta.json yet, so
    # model_ref is None and the outliner buries them in "Orphan sequences"
    # mid-run. Pull the parent model out of manifest.json (which IS
    # written at run start) as a fallback so the user sees the new run
    # nested under cluster_6_15 the moment it begins.
    if d.get("model_ref") is None:
        manifest_path = seq.path / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text())
                md = manifest.get("model_dir")
                if isinstance(md, str) and md:
                    d["model_ref"] = Path(md).name
            except (json.JSONDecodeError, OSError):
                pass
    d.setdefault("fps_hint", 24)
    d.setdefault("n_splats", None)
    d.setdefault("coord_convention", "z-up")
    d.setdefault("first_frame_full", True)
    d.setdefault("created_at", None)
    d.setdefault("converted_from", None)
    # `frame_count` resolution priority:
    #   1. meta-declared (cheap: already in the dict)
    #   2. live filesystem count (only when meta is absent OR a sim run
    #      is actively writing frames into this dir — we WANT the live
    #      count then so the playback bar grows as new frames land).
    # Before this guard the filesystem walk ran for every sequence on
    # every /api/sequences GET (polled every 5s), turning to 6k stats/s
    # for a 30-sequence library.
    if "frame_count" not in d:
        d["frame_count"] = seq.frame_count()
    else:
        is_live = seq.name in active_names
        if is_live:
            d["frame_count"] = seq.frame_count()
    # Cache descriptor: lets the SPA detect what's already built without
    # having to HEAD-probe each artifact. splats.gsq only exists after
    # pack_splats.py has run; frames.bin only exists after
    # server/tools/pack_sequence.py has run. Missing → field stays null.
    d["cache"] = {
        "splats_gsq_mtime": _stat_mtime(_VISER_CACHE / f"{seq.name}.gsq"),
        "splats_gsq_bytes": _stat_size(_VISER_CACHE / f"{seq.name}.gsq"),
        "frames_bin_mtime": _stat_mtime(lib.SEQUENCES_DIR / seq.name / "frames.bin"),
        "frames_bin_bytes": _stat_size(lib.SEQUENCES_DIR / seq.name / "frames.bin"),
    }
    return d


def _stat_mtime(p: Path) -> float | None:
    """`p.stat().st_mtime` or None if the file doesn't exist. Used by the
    sequence-list payload so the client sync daemon can compare against
    its local copy without a full HEAD round-trip per file."""
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _stat_size(p: Path) -> int | None:
    """`p.stat().st_size` or None if the file doesn't exist."""
    try:
        return p.stat().st_size
    except OSError:
        return None


def _active_sequence_names(run_mgr: RunManager) -> set[str]:
    """Names of currently-running sequences. Read once per request so a
    100-sequence library doesn't pay the per-entry cost in /api/sequences."""
    return {s.sequence_name for s in run_mgr.list_active() if s.sequence_name}


@router.get("")
def list_sequences(
    run_mgr: RunManager = Depends(_get_run_mgr),
):
    """List every sequence in the library, both sim-produced and imported.

    Newest-first by `created_at` (falls back to dir mtime when missing).
    """
    out: list[dict] = []
    active = _active_sequence_names(run_mgr)
    for name in Sequence.list():
        seq = Sequence.load(name)
        if seq is None:
            continue
        out.append(_sequence_dict(seq, active_names=active))

    def _sort_key(d: dict) -> tuple[int, str]:
        # Newest first; sequences without a parseable created_at sink.
        ca = d.get("created_at")
        if isinstance(ca, str):
            try:
                from datetime import datetime
                t = datetime.strptime(ca, "%Y-%m-%dT%H:%M:%SZ").timestamp()
                return (-int(t), d.get("name", ""))
            except (ValueError, OSError):
                pass
        # Fall back to dir mtime so unmigrated sequences aren't all tied at 0.
        try:
            t = (lib.SEQUENCES_DIR / d["name"]).stat().st_mtime
            return (-int(t), d.get("name", ""))
        except OSError:
            return (0, d.get("name", ""))

    out.sort(key=_sort_key)
    return out


class ImportRequest(BaseModel):
    folder_path: str
    name: str | None = None
    convert_y_up: bool = False


@router.post("/import")
def import_endpoint(
    req: ImportRequest,
    run_mgr: RunManager = Depends(_get_run_mgr),
):
    """Register an external folder of frame_*.ply as a Sequence.

    Returns the same dict shape as the list endpoint so the frontend can
    splice the new entry into its query cache without a full refetch.
    """
    folder = Path(req.folder_path)
    if not folder.exists():
        raise HTTPException(422, f"folder does not exist: {req.folder_path}")
    if not folder.is_dir():
        raise HTTPException(422, f"not a directory: {req.folder_path}")

    try:
        seq = import_sequence(folder, name=req.name, convert_y_up=req.convert_y_up)
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
    except (ImportError, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(422, str(e)) from e
    except NotADirectoryError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:
        # Surface plyfile parse errors as 422, disk-full as 500.
        # plyfile raises a generic ValueError/Exception subclass for
        # malformed input; the message tells us which.
        msg = str(e)
        if "ply" in msg.lower() or "header" in msg.lower():
            raise HTTPException(422, f"failed to parse ply: {msg}") from e
        if isinstance(e, OSError):
            raise HTTPException(500, f"disk error during import: {msg}") from e
        raise HTTPException(500, f"import failed: {msg}") from e

    return _sequence_dict(seq, active_names=_active_sequence_names(run_mgr))


@router.get("/{name}/frame/{frame_idx}.ply")
async def get_frame(name: str, frame_idx: int):
    """Re-exposes /api/runs/{name}/frame/{idx}.ply under the sequences
    namespace so the frontend's WebSocket bootstrap can hit either URL
    shape and get the same bytes."""
    return await _get_run_frame(name, frame_idx)


# .gsq files are immutable per (name, size, mtime): once produced for a
# given sequence, the bytes don't change. We surface that with a weak
# ETag and Cache-Control: immutable so the viser_headless client can
# short-circuit on HEAD when its local copy is current.
_GSQ_CACHE_CONTROL = "public, immutable, max-age=31536000"


def _gsq_etag(size: int, mtime: float) -> str:
    """Weak ETag '"<size>-<mtime_int>"' — matches the client's
    _local_etag() formula in frontend/python/viser_headless.py."""
    return f'"{size}-{int(mtime)}"'


def _serve_cache_gsq(name: str, filename: str, request: Request) -> Response:
    """Serve <filename> from the viser cache with weak ETag + Range + 304.

    `filename` is the on-disk basename (e.g. f"{name}.gsq").
    404 if the file is absent.
    """
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")
    path = _VISER_CACHE / filename
    if not path.is_file():
        raise HTTPException(
            404,
            f".gsq not built for '{name}'. Run "
            f"`python server/tools/pack_splats.py {name}` on the server.",
        )
    target = path.resolve()
    cache_root = _VISER_CACHE.resolve()
    try:
        target.relative_to(cache_root)
    except ValueError:
        raise HTTPException(400, f"refusing to serve outside cache: {filename}") from None
    st = target.stat()
    etag = _gsq_etag(st.st_size, st.st_mtime)
    if request.headers.get("if-none-match") == etag:
        # 304 carries no body but must repeat ETag + Cache-Control so
        # downstream caches stay consistent.
        return Response(status_code=304,
                        headers={"etag": etag, "cache-control": _GSQ_CACHE_CONTROL})
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=filename,
        headers={"etag": etag, "cache-control": _GSQ_CACHE_CONTROL},
    )


@router.get("/{name}/cache/splats.gsq")
def get_splats_gsq(name: str, request: Request):
    """Serve the full .gsq visual-lossless streamable cache (Range + ETag).

    Produced by `server/tools/pack_splats.py` as a smaller, byte-range
    addressable alternative to the .npz cache. Typical size: 0.4-1 GB
    vs 2.9 GB for the npz on the same sequence (~3-7x smaller). Same
    Range support via FileResponse — interrupted downloads resume
    it natively.

    Headers:
      Cache-Control: public, immutable, max-age=31536000
      ETag: "<size>-<mtime_int>"

    Conditional GET:
      If-None-Match matches current ETag -> 304 (no body).

    Falls through to 404 with a build hint if the .gsq doesn't exist yet.
    """
    return _serve_cache_gsq(name, f"{name}.gsq", request)


# ── cache build (on-demand) ─────────────────────────────────────────────────
# When the client selects a sequence whose viser .npz hasn't been built yet,
# it POSTs /cache/build to kick off pack_splats.py server-side. The
# old flow was "tell the user to ssh in and run the script themselves",
# which is hostile UX. Now the backend owns the lifecycle and the client
# polls /cache/build-status until done, then downloads the artifact.
#
# Job state lives in memory only (lost on restart). That's fine because the
# subprocess writes its output to disk — on restart, the next /cache/build
# call sees the existing .npz and exits early.

_SEQ_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_build_jobs: dict[str, dict] = {}
_build_lock = Lock()


def _run_build_subprocess(name: str, job: dict) -> None:
    """Worker thread: build the .gsq cache for one sequence.

    Single pass since 2026-05-22: pack_splats.py now reads frame_*.ply
    directly. The .npz intermediate is gone from the build path — old
    .npz files on disk still play (fallback paths in viser_headless +
    SPA), but new builds only emit .gsq.
    """
    gsq_tool = PKG_ROOT / "server" / "tools" / "pack_splats.py"
    try:
        r = subprocess.run(
            [sys.executable, str(gsq_tool), name],
            capture_output=True, text=True, timeout=600,
        )
        with _build_lock:
            job["finished_at"] = time.time()
            job["stdout_tail"] = (r.stdout or "")[-1500:]
            if r.returncode == 0:
                job["state"] = "done"
            else:
                job["state"] = "error"
                job["error"] = ((r.stderr or "")[-500:]
                                or f"pack_splats exit {r.returncode}")
    except subprocess.TimeoutExpired:
        with _build_lock:
            job["state"] = "error"
            job["error"] = "timeout"
            job["finished_at"] = time.time()
    except Exception as e:
        with _build_lock:
            job["state"] = "error"
            job["error"] = repr(e)
            job["finished_at"] = time.time()


@router.post("/{name}/cache/build")
def build_viser_cache(name: str) -> dict:
    """Kick off the viser .npz build for `name` as a background subprocess.

    Idempotent: if a build is already running for this sequence, returns
    the existing job state without spawning a duplicate. If the .npz
    already exists on disk, returns `{state: "done"}` immediately.

    Poll `GET /cache/build-status/{name}` for progress.
    """
    if not _SEQ_NAME_RE.match(name):
        raise HTTPException(422, f"invalid sequence name: {name!r}")
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")

    # Fast path: cache already on disk.
    if (_VISER_CACHE / f"{name}.gsq").is_file():
        return {"name": name, "state": "done",
                "note": "cache already exists on disk"}

    with _build_lock:
        existing = _build_jobs.get(name)
        if existing and existing.get("state") == "building":
            return existing
        job: dict = {
            "name": name,
            "state": "building",
            "started_at": time.time(),
            "finished_at": None,
            "stdout_tail": "",
            "error": None,
        }
        _build_jobs[name] = job

    threading.Thread(
        target=_run_build_subprocess, args=(name, job), daemon=True
    ).start()
    return job


@router.get("/{name}/cache/build-status")
def get_viser_cache_build_status(name: str) -> dict:
    """Poll the build job for `name`.

    States:
      - `"idle"`     no build has ever been requested in this process
      - `"building"` subprocess is running
      - `"done"`     subprocess exited 0, .npz is on disk
      - `"error"`    subprocess failed; `error` field has the tail of stderr
    """
    if not _SEQ_NAME_RE.match(name):
        raise HTTPException(422, f"invalid sequence name: {name!r}")
    with _build_lock:
        job = _build_jobs.get(name)
        if job is not None:
            return dict(job)
    # No job tracked → reflect disk state.
    if (_VISER_CACHE / f"{name}.gsq").is_file():
        return {"name": name, "state": "done", "note": "cache exists (no job tracked)"}
    return {"name": name, "state": "idle"}


@router.get("/{name}/cache/frames.bin")
def get_frames_bin_cache(name: str):
    """Serve the GSSQ-packed frames.bin (int16-quantized xyz per frame).

    Mirror of `server/tools/pack_sequence.py`'s output, used by the client
    Points-mode WS server (`frontend/python/local_stream.py`) for fast local
    streaming without per-frame ply reads. Same range-resume semantics
    as the viser cache endpoint."""
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")
    path = lib.SEQUENCES_DIR / name / "frames.bin"
    if not path.is_file():
        raise HTTPException(
            404,
            f"frames.bin not built for sequence '{name}'. "
            f"Run `python server/tools/pack_sequence.py {name}` on the server.",
        )
    target = path.resolve()
    seq_root = lib.SEQUENCES_DIR.resolve()
    try:
        target.relative_to(seq_root)
    except ValueError:
        raise HTTPException(400, f"refusing to serve outside library: {name}") from None
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=f"{name}.bin",
    )


@router.delete("/{name}")
def delete_sequence(
    name: str,
    run_mgr: RunManager = Depends(_get_run_mgr),
):
    """Remove a sequence from the library.

    For imports (where frames/ is a symlink), only the library entry +
    symlink are removed — the source folder is never touched. For sim-
    produced sequences (real dirs), the whole library entry is rmtree'd.

    Refuses to delete a still-running sim's output dir.
    """
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")

    # Path-traversal defense.
    target = (lib.SEQUENCES_DIR / name).resolve()
    seq_root = lib.SEQUENCES_DIR.resolve()
    try:
        target.relative_to(seq_root)
    except ValueError:
        raise HTTPException(400, f"refusing to delete outside library: {name}") from None

    if name in _active_sequence_names(run_mgr):
        raise HTTPException(
            409, f"sequence is still being written: {name}; cancel the run first",
        )

    if not Sequence.delete(name):
        raise HTTPException(500, f"failed to delete sequence: {name}")
    return {"deleted": name}
