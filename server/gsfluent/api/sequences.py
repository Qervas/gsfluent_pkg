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
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
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
    # (e.g. <sequences-root>/foo/) into every payload.
    # The React workbench doesn't consume it; under split-topology the
    # laptop has no use for the server's local path either. Stripping
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


@router.post("/upload-npz")
async def upload_npz(
    file: UploadFile = File(..., description="A pre-built .npz playback cache"),
    name: str | None = Form(None, description="Sequence name (defaults to basename without .npz)"),
):
    """Accept a drag-dropped .npz from the workbench and register it as
    a Sequence in the library — the laptop-local sibling of POST
    /api/models/upload but for sequence playback caches instead of
    .ply models. After this returns, the new sequence shows up in
    /api/sequences and is mmap'd by viser_headless on its next /reload.

    Validation strategy:
      1. Magic-byte sniff (PK\\x03\\x04 = zip header used by .npz).
      2. Stream the upload to a temp file under work/cache/viser/.
      3. Open with np.load(mmap_mode="r") to verify shape — derives
         frame_count from the first per-frame array's axis 0 and
         n_splats from axis 1. Works for both v1 (cov) and v2 (quats)
         schemas because both have `frames: (T, N, 3)`.
      4. Atomic rename to <name>.npz, then write a stub _meta.json
         under work/library/sequences/<name>/ so the outliner shows
         the sequence with provenance.

    Size cap: 8 GB (our largest production .npz is ~3 GB; doubled for
    headroom). Upload streams to disk via UploadFile's spooled buffer
    — never holds the whole file in process memory.
    """
    import shutil
    import tempfile
    import numpy as np

    fname = file.filename or ""
    if not fname.lower().endswith(".npz"):
        raise HTTPException(422, "file must have a .npz extension")
    seq_name = (name or fname[: -len(".npz")]).strip()
    if not seq_name or "/" in seq_name or seq_name.startswith("."):
        raise HTTPException(422, f"invalid sequence name: {seq_name!r}")

    _VISER_CACHE.mkdir(parents=True, exist_ok=True)
    final_path = _VISER_CACHE / f"{seq_name}.npz"

    if Sequence.exists(seq_name) or final_path.exists():
        raise HTTPException(
            409,
            f"sequence already exists: {seq_name}. "
            f"Delete it first or upload under a different name.",
        )

    SIZE_CAP = 8 * 1024 * 1024 * 1024  # 8 GB
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"{seq_name}.", suffix=".npz.partial",
        dir=str(_VISER_CACHE), delete=False,
    )
    try:
        total = 0
        while True:
            chunk = await file.read(4 * 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > SIZE_CAP:
                raise HTTPException(
                    413, f"upload exceeds {SIZE_CAP // (1024**3)} GB cap",
                )
            tmp.write(chunk)
        tmp.flush()
        tmp.close()

        with open(tmp.name, "rb") as f:
            magic = f.read(4)
        if magic != b"PK\x03\x04":
            raise HTTPException(422, "file is not a valid .npz (zip header missing)")

        # numpy defaults to refusing object arrays (allow_pickle defaults
        # False since 1.16.4) — anything weird in the payload will raise
        # below rather than execute.
        try:
            d = np.load(tmp.name, mmap_mode="r")
            keys = set(d.files)
            if "frames" not in keys:
                raise HTTPException(422, "npz is missing the 'frames' array")
            frames_shape = d["frames"].shape
            if len(frames_shape) != 3 or frames_shape[2] != 3:
                raise HTTPException(
                    422,
                    f"'frames' has shape {frames_shape}; expected (T, N, 3)",
                )
            frame_count = int(frames_shape[0])
            n_splats = int(frames_shape[1])
            del d
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(422, f"npz parse failed: {e}")

        shutil.move(tmp.name, final_path)
    except HTTPException:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass
        raise

    Sequence.write_meta(
        name=seq_name,
        source="import",
        source_path=f"upload:{fname}",
        model_ref=None,
        frame_count=frame_count,
        n_splats=n_splats,
        coord_convention="z-up",
        first_frame_full=True,
    )
    seq = Sequence.load(seq_name)
    if seq is None:
        raise HTTPException(500, "write_meta succeeded but Sequence.load returned None")
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
