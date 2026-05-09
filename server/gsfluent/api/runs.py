"""Runs API — start/cancel/list active sims plus history-of-past-sequences.

Past runs live in the library at `work/library/sequences/<name>/`. We list
them by walking `library.SEQUENCES_DIR` and reading each sequence's
`_meta.json` (and, where present, the original `manifest.json` carried
over from the runner — it has `started_at`, `status`, `particles`,
`recipe_source` which the HistoryEntry frontend type expects).

The frontend HistoryEntry contract (frontend/src/lib/types.ts) is:
  { run_name, status, started_at, finished_at?, particles?, recipe_source? }
We preserve every field.
"""
import json
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import library as lib
from ..core import runner
from ..core.library import Sequence

router = APIRouter(prefix="/api/runs", tags=["runs"])


class StartRunRequest(BaseModel):
    run_name: str
    model_path: str
    recipe_data: dict
    recipe_source: str
    particles: int = 200_000


@router.get("")
def list_active():
    return [{"id": r.id, "name": r.name, "state": r.state} for r in runner.list_runs()]


@router.post("")
async def start(req: StartRunRequest):
    model_dir = Path(req.model_path)
    if not model_dir.exists():
        raise HTTPException(422, f"model_path does not exist: {req.model_path}")
    if not model_dir.is_dir():
        raise HTTPException(422, f"model_path is not a directory: {req.model_path}")
    try:
        rid = await runner.start_run(
            run_name=req.run_name,
            model_dir=model_dir,
            recipe_data=req.recipe_data,
            recipe_source_name=req.recipe_source,
            particles=req.particles,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
        raise HTTPException(422, f"failed to start run: {e}")
    return {"run_id": rid, "run_name": req.run_name}


@router.delete("/{run_id}")
def cancel(run_id: str):
    if not runner.cancel_run(run_id):
        raise HTTPException(404, f"run {run_id} not active")
    return {"status": "cancelled"}


def _seq_root() -> Path:
    """Resolve the sequences root.

    Looks first at `runner.FUSED_DIR` for backward compat with tests that
    monkeypatch it (those tests build a `<tmp>/fused/<name>/manifest.json`
    layout pre-Phase-1). If FUSED_DIR is the real PKG_ROOT/work/fused dir
    (the legacy production location) we ignore it — production reads
    library.SEQUENCES_DIR. Tests pointing FUSED_DIR at a temp path keep
    working because their layout is what the legacy branch reads.
    """
    return lib.SEQUENCES_DIR


@router.delete("/history/{run_name}")
def delete_history(run_name: str):
    """Delete a single past run from the library by name.

    Path-traversal defense: a run_name like '../../etc' is rejected
    before any rmtree. Refuses to delete a still-running run (the
    in-process registry would still hold a subprocess + log handle).
    """
    if not Sequence.exists(run_name):
        # Fall back to the legacy fused dir for tests / pre-migration data.
        legacy = (runner.FUSED_DIR / run_name).resolve()
        try:
            legacy.relative_to(runner.FUSED_DIR.resolve())
        except ValueError:
            raise HTTPException(400, f"refusing to delete outside library: {run_name}")
        if not legacy.exists():
            raise HTTPException(404, f"run not found: {run_name}")
        if not legacy.is_dir():
            raise HTTPException(400, f"not a run directory: {run_name}")
        for r in runner.list_runs():
            if r.name == run_name and r.state == "running":
                raise HTTPException(
                    409, f"run is still running: {run_name}; cancel it first",
                )
        try:
            shutil.rmtree(legacy)
        except OSError as e:
            raise HTTPException(500, f"failed to delete run dir: {e}")
        return {"deleted": run_name}

    # Path-traversal defense for the library path.
    target = (lib.SEQUENCES_DIR / run_name).resolve()
    seq_root = lib.SEQUENCES_DIR.resolve()
    try:
        target.relative_to(seq_root)
    except ValueError:
        raise HTTPException(400, f"refusing to delete outside library: {run_name}")

    for r in runner.list_runs():
        if r.name == run_name and r.state == "running":
            raise HTTPException(
                409, f"run is still running: {run_name}; cancel it first",
            )

    if not Sequence.delete(run_name):
        raise HTTPException(500, f"failed to delete sequence: {run_name}")
    return {"deleted": run_name}


_RUN_FRAME_RE = re.compile(r"^frame_(\d+)\.ply$")


@router.get("/{run_name}/frame/{frame_idx}.ply")
async def get_run_frame(run_name: str, frame_idx: int):
    """Serve a single frame .ply for a sequence.

    Used by the splat-mode playback to bootstrap the in-browser splat
    mesh: the WS pump streams xyz-only updates per frame, but the splat
    library needs the full attribute set (scales, rotations, opacity,
    SH) from frame 0 to build its render pipeline.
    """
    seq = Sequence.load(run_name)
    if seq is not None:
        ply_path = seq.frames_dir() / f"frame_{frame_idx:04d}.ply"
        if ply_path.is_file():
            return FileResponse(
                str(ply_path),
                media_type="application/octet-stream",
                filename=ply_path.name,
            )
        raise HTTPException(404, f"frame {frame_idx} not found in sequence {run_name}")

    # Fallback to the legacy fused dir for tests + pre-migration data.
    target_dir = (runner.FUSED_DIR / run_name).resolve()
    fused_root = runner.FUSED_DIR.resolve()
    try:
        target_dir.relative_to(fused_root)
    except ValueError:
        raise HTTPException(400, f"refusing to read outside FUSED_DIR: {run_name}")
    if not target_dir.is_dir():
        raise HTTPException(404, f"run not found: {run_name}")
    candidates = [
        target_dir / f"frame_{frame_idx:04d}.ply",
        target_dir / "frames" / f"frame_{frame_idx:04d}.ply",
    ]
    for ply_path in candidates:
        if ply_path.is_file():
            return FileResponse(
                str(ply_path),
                media_type="application/octet-stream",
                filename=ply_path.name,
            )
    raise HTTPException(404, f"frame {frame_idx} not found in run {run_name}")


def _history_entry_from_sequence(seq: Sequence) -> dict | None:
    """Build a HistoryEntry-shaped dict from a Sequence + its sibling
    `manifest.json` (left there by the runner). Returns None if the
    sequence is unreadable enough that we can't produce even the minimal
    `run_name + status` shape.

    Field provenance:
      - run_name: sequence name (=== seq.name)
      - status: manifest.json:status if present, else "done" if frames
        exist, else "unknown"
      - started_at: manifest.json:started_at if present (epoch float),
        else parsed from _meta.json:created_at (ISO string -> epoch),
        else dir mtime
      - finished_at: manifest.json:finished_at if present
      - particles: manifest.json:particles if present
      - recipe_source: manifest.json:recipe_source if present
    """
    manifest_path = seq.path / "manifest.json"
    manifest: dict = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict):
                manifest = {}
        except (json.JSONDecodeError, OSError):
            manifest = {}

    meta = seq.meta or {}
    frame_count = seq.frame_count()

    started_at = manifest.get("started_at")
    if started_at is None:
        # Try _meta.json:created_at (ISO 8601 UTC).
        ca = meta.get("created_at")
        if isinstance(ca, str):
            try:
                from datetime import datetime
                started_at = datetime.strptime(
                    ca, "%Y-%m-%dT%H:%M:%SZ"
                ).timestamp()
            except (ValueError, OSError):
                started_at = None
        if started_at is None:
            try:
                started_at = seq.path.stat().st_mtime
            except OSError:
                started_at = 0.0

    status = manifest.get("status")
    if not status:
        status = "done" if frame_count > 0 else "unknown"

    out: dict = {
        "run_name": seq.name,
        "status": status,
        "started_at": started_at,
    }
    if "finished_at" in manifest:
        out["finished_at"] = manifest["finished_at"]
    if "particles" in manifest:
        out["particles"] = manifest["particles"]
    if "recipe_source" in manifest:
        out["recipe_source"] = manifest["recipe_source"]
    # Carry the new metadata fields too so future Phase-2+ frontend
    # code can read them without bumping the type. Frontend ignores
    # extras today.
    if meta.get("model_ref"):
        out["model_ref"] = meta["model_ref"]
    if "frame_count" in meta:
        out["frame_count"] = meta["frame_count"]
    elif frame_count:
        out["frame_count"] = frame_count
    if meta.get("source"):
        out["sequence_source"] = meta["source"]
    return out


def _history_entry_from_legacy_dir(d: Path) -> dict | None:
    """Build a HistoryEntry-shaped dict from a pre-Phase-1 fused dir.

    Used by tests that monkeypatch `runner.FUSED_DIR` to a tmp dir
    containing `<run>/manifest.json` files in the old layout, and as a
    fallback for any production data that wasn't run through the migration
    script yet (shouldn't happen, but defensive).
    """
    m = d / "manifest.json"
    if m.is_file():
        try:
            data = json.loads(m.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("run_name", d.name)
        return data
    # Legacy frame-only dir (no manifest, no _meta.json).
    frame_count = sum(1 for _ in d.glob("frame_*.ply")) + sum(
        1 for _ in d.glob("frames/frame_*.ply")
    )
    if frame_count == 0:
        return None
    try:
        mtime = d.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "run_name": d.name,
        "status": "done",
        "started_at": mtime,
        "finished_at": mtime,
        "particles": None,
        "recipe_source": None,
        "_synthetic": True,
    }


@router.get("/history")
def history():
    """List all past runs in the library, newest-first.

    Walks `library.SEQUENCES_DIR` and merges each sequence's `_meta.json`
    + (where present) `manifest.json` into a HistoryEntry-shaped dict.
    Falls back to the legacy `runner.FUSED_DIR` walk for any pre-migration
    data; tests patch `FUSED_DIR` to a tmp dir.
    """
    out: list[dict] = []
    seen_names: set[str] = set()

    if lib.SEQUENCES_DIR.is_dir():
        for name in Sequence.list():
            seq = Sequence.load(name)
            if seq is None:
                continue
            entry = _history_entry_from_sequence(seq)
            if entry is None:
                continue
            out.append(entry)
            seen_names.add(name)

    # Legacy/fallback walk. Tests monkeypatch FUSED_DIR to a tmp path
    # holding the old `<run>/manifest.json` layout — surface those too.
    fused = runner.FUSED_DIR
    if fused.is_dir() and fused.resolve() != lib.SEQUENCES_DIR.resolve():
        try:
            for d in sorted(
                fused.iterdir(),
                key=lambda p: (-p.stat().st_mtime, p.name),
            ):
                if not d.is_dir() or d.name in seen_names:
                    continue
                entry = _history_entry_from_legacy_dir(d)
                if entry is not None:
                    out.append(entry)
                    seen_names.add(d.name)
        except OSError:
            pass

    # Sort newest-first by `started_at` (manifest field) so the UI
    # ordering matches the previous behavior.
    out.sort(key=lambda e: e.get("started_at") or 0, reverse=True)
    return out
