"""Sequences API — list / import / serve frames / delete library sequences.

Both sim-produced sequences (source="sim", written by the runner) and
external imports (source="import", symlinked via library.import_sequence)
live in the same dir at `work/library/sequences/<name>/`. The Outliner
surfaces them in a single tree; this router is the unified backend.

The frame-serving endpoint is intentionally aliased to the existing one
in api/runs.py so the frontend WebSocket bootstrap (which hardcodes
`/api/runs/<name>/frame/0.ply`) keeps working — see `api/runs.py:get_run_frame`.
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import library as lib
from ..core import runner
from ..core.library import Sequence, import_sequence
from ..server import PKG_ROOT
# Frame-serving handler is shared with /api/runs/{name}/frame/{idx}.ply
# so the two URL shapes return the exact same bytes for the same args.
# Imported at the top (was originally a late-import inside the function
# to avoid circular-import worries — verified no longer needed).
from .runs import get_run_frame as _get_run_frame

router = APIRouter(prefix="/api/sequences", tags=["sequences"])

# Where derived caches live. The viser .npz cache is built by
# `tools/batch_convert_to_npz.py` and consumed by `tools/viser_headless.py`.
# Under the split-topology deployment, the server holds the canonical
# copy and laptop-side `tools/sync_daemon.py` mirrors files here onto
# the laptop's local cache.
_VISER_CACHE = PKG_ROOT / "work" / "cache" / "viser"


def _sequence_dict(seq: Sequence) -> dict:
    """Build the frontend-facing sequence dict.

    Carries everything in `_meta.json` plus `is_broken` (computed from the
    frames symlink state). Frame count is taken from meta when present,
    falling back to a live filesystem count for sim-produced sequences
    that may be growing as we read.
    """
    d = seq.meta_dict()
    # Always emit is_broken — frontend reads it to decide whether to show
    # the warning indicator.
    d["is_broken"] = bool(seq.is_broken)
    # `meta_dict()` injects the absolute server filesystem path
    # (e.g. /data/yinshaoxuan/.../sequences/foo/) into every payload.
    # The React workbench doesn't consume it; under split-topology the
    # laptop has no use for the server's local path either. Stripping
    # it keeps the API surface from leaking server directory layout.
    d.pop("path", None)
    # Default-fill the fields the frontend SequenceItem type expects, so
    # legacy sequences without a complete _meta.json still render.
    d.setdefault("source", "unknown")
    d.setdefault("source_path", None)
    d.setdefault("model_ref", None)
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
        is_live = any(r.name == seq.name and r.state == "running"
                      for r in runner.list_runs())
        if is_live:
            d["frame_count"] = seq.frame_count()
    # Cache descriptor: lets the laptop sync daemon detect staleness
    # without having to download anything to check. Both files are
    # optional — viser.npz only exists after batch_convert_to_npz.py
    # has run; frames.bin only exists after tools/pack_sequence.py has
    # run. Missing → field stays null and the daemon skips that file.
    d["cache"] = {
        "viser_npz_mtime":  _stat_mtime(_VISER_CACHE / f"{seq.name}.npz"),
        "viser_npz_bytes":  _stat_size(_VISER_CACHE / f"{seq.name}.npz"),
        "frames_bin_mtime": _stat_mtime(lib.SEQUENCES_DIR / seq.name / "frames.bin"),
        "frames_bin_bytes": _stat_size(lib.SEQUENCES_DIR / seq.name / "frames.bin"),
    }
    return d


def _stat_mtime(p: Path) -> Optional[float]:
    """`p.stat().st_mtime` or None if the file doesn't exist. Used by the
    sequence-list payload so the laptop sync daemon can compare against
    its local copy without a full HEAD round-trip per file."""
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _stat_size(p: Path) -> Optional[int]:
    """`p.stat().st_size` or None if the file doesn't exist."""
    try:
        return p.stat().st_size
    except OSError:
        return None


@router.get("")
def list_sequences():
    """List every sequence in the library, both sim-produced and imported.

    Newest-first by `created_at` (falls back to dir mtime when missing).
    """
    out: list[dict] = []
    for name in Sequence.list():
        seq = Sequence.load(name)
        if seq is None:
            continue
        out.append(_sequence_dict(seq))

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
    name: Optional[str] = None
    convert_y_up: bool = False


@router.post("/import")
def import_endpoint(req: ImportRequest):
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
        raise HTTPException(409, str(e))
    except (ImportError, ValueError) as e:
        raise HTTPException(422, str(e))
    except FileNotFoundError as e:
        raise HTTPException(422, str(e))
    except NotADirectoryError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        # Surface plyfile parse errors as 422, disk-full as 500.
        # plyfile raises a generic ValueError/Exception subclass for
        # malformed input; the message tells us which.
        msg = str(e)
        if "ply" in msg.lower() or "header" in msg.lower():
            raise HTTPException(422, f"failed to parse ply: {msg}")
        if isinstance(e, OSError):
            raise HTTPException(500, f"disk error during import: {msg}")
        raise HTTPException(500, f"import failed: {msg}")

    return _sequence_dict(seq)


@router.get("/{name}/frame/{frame_idx}.ply")
async def get_frame(name: str, frame_idx: int):
    """Re-exposes /api/runs/{name}/frame/{idx}.ply under the sequences
    namespace so the frontend's WebSocket bootstrap can hit either URL
    shape and get the same bytes."""
    return await _get_run_frame(name, frame_idx)


@router.get("/{name}/cache/viser.npz")
def get_viser_cache(name: str):
    """Serve the .npz viser cache file as a downloadable artifact.

    Used by the laptop sync daemon (`tools/sync_daemon.py`) to mirror the
    server's cache onto the laptop, where `tools/viser_headless.py`
    mmaps it for Splats-mode playback. The split-topology rationale:
    pushing per-frame xyz over WAN at 30 fps is ~2 Gbps (683k splats ×
    12 B × 30) — infeasible. A one-time .npz download (~1-2 GB) then
    local playback is the only path that scales.

    FastAPI's FileResponse handles Range requests natively, so
    interrupted downloads resume cleanly.
    """
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")
    path = _VISER_CACHE / f"{name}.npz"
    if not path.is_file():
        raise HTTPException(
            404,
            f"viser cache not built for sequence '{name}'. "
            "Run `python tools/batch_convert_to_npz.py {name}` on the server.",
        )
    # Path-traversal defense: confirm the resolved path is inside the cache.
    target = path.resolve()
    cache_root = _VISER_CACHE.resolve()
    try:
        target.relative_to(cache_root)
    except ValueError:
        raise HTTPException(400, f"refusing to serve outside cache: {name}")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=f"{name}.npz",
    )


@router.get("/{name}/cache/frames.bin")
def get_frames_bin_cache(name: str):
    """Serve the GSSQ-packed frames.bin (int16-quantized xyz per frame).

    Mirror of `tools/pack_sequence.py`'s output, used by the laptop
    Points-mode WS server (`tools/local_stream.py`) for fast local
    streaming without per-frame ply reads. Same range-resume semantics
    as the viser cache endpoint."""
    if not Sequence.exists(name):
        raise HTTPException(404, f"sequence not found: {name}")
    path = lib.SEQUENCES_DIR / name / "frames.bin"
    if not path.is_file():
        raise HTTPException(
            404,
            f"frames.bin not built for sequence '{name}'. "
            f"Run `python tools/pack_sequence.py {name}` on the server.",
        )
    target = path.resolve()
    seq_root = lib.SEQUENCES_DIR.resolve()
    try:
        target.relative_to(seq_root)
    except ValueError:
        raise HTTPException(400, f"refusing to serve outside library: {name}")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=f"{name}.bin",
    )


@router.delete("/{name}")
def delete_sequence(name: str):
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
        raise HTTPException(400, f"refusing to delete outside library: {name}")

    for r in runner.list_runs():
        if r.name == name and r.state == "running":
            raise HTTPException(
                409, f"sequence is still being written: {name}; cancel the run first",
            )

    if not Sequence.delete(name):
        raise HTTPException(500, f"failed to delete sequence: {name}")
    return {"deleted": name}
