import json
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import models as m

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_endpoint():
    return m.list_models()


@router.post("/upload")
async def upload(
    ply: UploadFile = File(..., description=".ply file"),
    cameras_json: UploadFile | None = File(None, description="optional cameras.json"),
    convert_y_up: bool = Form(False, description="rewrite Y-up source to Z-up at import"),
):
    if not (ply.filename or "").lower().endswith(".ply"):
        raise HTTPException(422, "ply field must be a .ply file")
    content = await ply.read()  # TODO Phase 4: stream to disk for production-sized plys (>1GB)
    if len(content) < 64:
        raise HTTPException(422, "uploaded ply is too small to be valid")
    if not (content.startswith(b"ply\n") or content.startswith(b"ply\r")):
        raise HTTPException(422, "uploaded ply is missing magic header")

    cam_bytes: bytes | None = None
    if cameras_json is not None and (cameras_json.filename or ""):
        if not (cameras_json.filename or "").lower().endswith(".json"):
            raise HTTPException(422, "cameras_json field must be a .json file")
        cam_bytes = await cameras_json.read()
        try:
            parsed = json.loads(cam_bytes)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"cameras.json is not valid JSON: {e}")
        if not isinstance(parsed, list):
            raise HTTPException(422, "cameras.json must be a JSON list")

    try:
        name, path = m.wrap_ply_upload(
            ply.filename, content, cam_bytes, convert_y_up=convert_y_up,
        )
    except Exception as e:
        msg = str(e)
        # plyfile parse errors during the conversion pass: client should
        # see a 422 not 500 — the bytes they uploaded weren't a valid ply.
        if convert_y_up and ("ply" in msg.lower() or "header" in msg.lower()):
            raise HTTPException(422, f"failed to parse ply for conversion: {msg}")
        raise
    return {"name": name, "path": str(path)}


class RegisterRequest(BaseModel):
    path: str
    convert_y_up: bool = False


@router.post("/register")
async def register(req: RegisterRequest):
    try:
        name, path, mode = m.register_local_model(
            Path(req.path), convert_y_up=req.convert_y_up,
        )
    except FileNotFoundError as e:
        raise HTTPException(422, str(e))
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    return {"name": name, "path": str(path), "mode": mode}


_ITER_RE = re.compile(r"^iteration_(\d+)$")


@router.get("/file")
@router.get("/file/{filename}")
async def get_model_file(path: str, filename: str | None = None):
    """Stream the highest-iteration point_cloud.ply for a model dir.

    The proper 3DGS render path needs the raw ply file over HTTP so the
    in-browser splat library can parse the full attribute set (positions,
    SH coefficients, rotations, scales, opacity). The websocket
    /api/stream path only sends a stripped subset suitable for the points
    fallback renderer.

    The optional `{filename}` path segment is ignored — present only so
    the URL ends with .ply, which @mkkellogg/gaussian-splats-3d's
    sceneFormatFromPath helper expects for its parser dispatch."""
    del filename  # cosmetic only; format is always resolved from disk
    model_path = Path(path)
    if not model_path.is_dir():
        raise HTTPException(404, f"model dir not found: {path}")
    pc_root = model_path / "point_cloud"
    if not pc_root.is_dir():
        raise HTTPException(404, f"missing point_cloud/ subdir under {path}")
    candidates: list[tuple[int, Path]] = []
    for it in pc_root.iterdir():
        if not it.is_dir():
            continue
        match = _ITER_RE.match(it.name)
        if match and (it / "point_cloud.ply").is_file():
            candidates.append((int(match.group(1)), it / "point_cloud.ply"))
    if not candidates:
        raise HTTPException(404, f"no iteration_*/point_cloud.ply under {pc_root}")
    candidates.sort(key=lambda t: -t[0])
    ply_path = candidates[0][1]
    return FileResponse(
        str(ply_path),
        media_type="application/octet-stream",
        filename=ply_path.name,
    )
