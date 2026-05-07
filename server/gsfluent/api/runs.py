import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import runner

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


@router.get("/history")
def history():
    out: list[dict] = []
    if not runner.FUSED_DIR.exists():
        return out
    try:
        entries = sorted(
            runner.FUSED_DIR.iterdir(),
            key=lambda p: (-p.stat().st_mtime, p.name),
        )
    except OSError:
        return out
    for d in entries:
        if not d.is_dir():
            continue
        m = d / "manifest.json"
        if m.exists():
            try:
                data = json.loads(m.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            data.setdefault("run_name", d.name)
            out.append(data)
        else:
            # Legacy / pre-rewrite: synthesize a minimal manifest from the
            # frame files on disk. Allows old viser-era runs to surface in
            # the History panel without requiring a re-run.
            frame_count = sum(
                1 for _ in d.glob("frame_*.ply")
            ) + sum(
                1 for _ in d.glob("frames/frame_*.ply")
            )
            if frame_count == 0:
                continue
            try:
                mtime = d.stat().st_mtime
            except OSError:
                mtime = 0
            out.append({
                "run_name": d.name,
                "status": "done",
                "started_at": mtime,
                "finished_at": mtime,
                "particles": None,
                "recipe_source": None,
                "_synthetic": True,
            })
    return out
