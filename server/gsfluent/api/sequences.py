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
from pydantic import BaseModel

from ..core import library as lib
from ..core import runner
from ..core.library import Sequence, import_sequence

router = APIRouter(prefix="/api/sequences", tags=["sequences"])


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
    if "frame_count" not in d:
        d["frame_count"] = seq.frame_count()
    return d


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
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except (ImportError, ValueError) as e:
        raise HTTPException(422, str(e))
    except (FileNotFoundError, NotADirectoryError, OSError) as e:
        raise HTTPException(422, str(e))

    return _sequence_dict(seq)


# Frame-serving handler. We forward to the existing handler in api/runs.py
# rather than reimplement the legacy-fused-dir fallback path. Same URL
# pattern under /api/sequences for symmetry; the legacy /api/runs/.../frame
# alias stays live.
from .runs import get_run_frame as _get_run_frame  # noqa: E402  (intentional late import)


@router.get("/{name}/frame/{frame_idx}.ply")
async def get_frame(name: str, frame_idx: int):
    return await _get_run_frame(name, frame_idx)


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
