import gzip as _gz
import hashlib
import io
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import models as m
from ..core.library import Model

router = APIRouter(prefix="/api/models", tags=["models"])

_log = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 8 * 1024 ** 3
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


async def _read_upload_capped(
    upload,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    chunk_size: int = UPLOAD_CHUNK_BYTES,
) -> bytes:
    buf = bytearray()
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(413, f"upload too large: {len(buf)} > {max_bytes}")
    return bytes(buf)


def _gunzip_capped(
    content: bytes,
    *,
    max_bytes: int = MAX_UPLOAD_BYTES,
    chunk_size: int = UPLOAD_CHUNK_BYTES,
) -> bytes:
    try:
        with _gz.GzipFile(fileobj=io.BytesIO(content)) as gz:
            buf = bytearray()
            while True:
                chunk = gz.read(chunk_size)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise HTTPException(
                        413,
                        f"gunzipped ply exceeds {max_bytes} bytes; "
                        "looks like a gzip bomb",
                    )
            return bytes(buf)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"failed to gunzip uploaded ply: {e}") from e


@router.get("")
def list_endpoint():
    return m.list_models()


class HashCheckRequest(BaseModel):
    sha256: str
    filename: str | None = None  # diagnostic logging only — not persisted


@router.post("/check_hash")
def check_hash(req: HashCheckRequest):
    """Look up an existing model by content hash.

    Frontend calls this before uploading so a re-drop of the same .ply
    skips transport entirely. Returns {"exists": false} when no model
    in the library carries this sha256.
    """
    if not req.sha256 or len(req.sha256) != 64:
        raise HTTPException(422, "sha256 must be a 64-char hex string")
    existing = Model.find_by_hash(req.sha256)
    if existing is None:
        return {"exists": False}
    meta = existing.meta_dict()
    return {
        "exists": True,
        "name": existing.name,
        "path": str(existing.path),
        "n_splats": meta.get("n_splats"),
    }


@router.post("/upload")
async def upload(
    ply: UploadFile = File(..., description=".ply file"),
    cameras_json: UploadFile | None = File(None, description="optional cameras.json"),
    convert_y_up: bool = Form(False, description="rewrite Y-up source to Z-up at import"),
    ply_encoding: str = Form(
        "identity",
        description="transport encoding of the ply field: 'identity' or 'gzip'",
    ),
):
    if not (ply.filename or "").lower().endswith(".ply"):
        raise HTTPException(422, "ply field must be a .ply file")
    # Cap upload size in line with /upload-npz (8 GiB). Without a cap
    # `await ply.read()` will happily slurp a 100 GB body and OOM the
    # server before we can reject it.
    content = await _read_upload_capped(ply)

    # Decompress before validation. We signal via a Form field rather
    # than HTTP Content-Encoding because FastAPI / Starlette doesn't
    # auto-decompress request bodies, and intercepting the raw multipart
    # body to do so is a much bigger change than just gzipping the .ply
    # bytes in the browser before they go into FormData.
    #
    # Decompress via GzipFile with an output cap so a malicious gzip
    # bomb (a few-MB body that expands to many GB) can't OOM the server.
    if ply_encoding == "gzip":
        content = _gunzip_capped(content)
    elif ply_encoding != "identity":
        raise HTTPException(422, f"unsupported ply_encoding: {ply_encoding!r}")

    if len(content) < 64:
        raise HTTPException(422, "uploaded ply is too small to be valid")
    if not (content.startswith(b"ply\n") or content.startswith(b"ply\r")):
        raise HTTPException(422, "uploaded ply is missing magic header")

    # Defensive dedup: hash the decompressed bytes here too so a client
    # that skipped /check_hash (network blip, stale build) still can't
    # create a duplicate model entry.
    sha = hashlib.sha256(content).hexdigest()
    existing = Model.find_by_hash(sha)
    if existing is not None:
        return existing.meta_dict()

    cam_bytes: bytes | None = None
    if cameras_json is not None and (cameras_json.filename or ""):
        if not (cameras_json.filename or "").lower().endswith(".json"):
            raise HTTPException(422, "cameras_json field must be a .json file")
        cam_bytes = await cameras_json.read()
        try:
            parsed = json.loads(cam_bytes)
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"cameras.json is not valid JSON: {e}") from e
        if not isinstance(parsed, list):
            raise HTTPException(422, "cameras.json must be a JSON list")

    try:
        name, path = m.wrap_ply_upload(
            ply.filename, content, cam_bytes,
            convert_y_up=convert_y_up, sha256=sha,
        )
    except Exception as e:
        msg = str(e)
        # plyfile parse errors during the conversion pass: client should
        # see a 422 not 500 — the bytes they uploaded weren't a valid ply.
        if convert_y_up and ("ply" in msg.lower() or "header" in msg.lower()):
            raise HTTPException(422, f"failed to parse ply for conversion: {msg}") from e
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
        raise HTTPException(422, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
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
    sceneFormatFromPath helper expects for its parser dispatch.

    Security: the `path` query parameter is user-supplied. To prevent
    arbitrary-filesystem-read, we verify the path is a known registered
    model — either inside MODELS_DIR or an externally-registered entry
    in _registered.json. An unrecognized path returns 404 without ever
    touching the filesystem.
    """
    del filename  # cosmetic only; format is always resolved from disk
    model_path = Path(path).resolve()
    # Allowlist check: enumerate known models and require an exact match.
    # Cheaper than walking the FS and tighter than a MODELS_DIR.relative_to
    # check (which would refuse externally-registered paths).
    known_paths = {
        Path(entry["path"]).resolve()
        for entry in m.list_models()
        if entry.get("path")
    }
    if model_path not in known_paths:
        raise HTTPException(404, f"model path not registered: {path}")
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


@router.delete("/{name}")
def delete_endpoint(name: str):
    """Remove a model from the library.

    For internally-stored models (uploaded via /upload), the model
    directory under MODELS_DIR is recursively deleted. For externally-
    registered models (registered via /register pointing at an existing
    on-disk path), only the registry entry is dropped — we never touch
    user files outside the library root. Sequences referencing this
    model by `model_ref` are NOT cascaded; they become orphans.
    """
    if not Model.exists(name):
        raise HTTPException(404, f"model not found: {name}")
    ok = Model.delete(name)
    if not ok:
        raise HTTPException(500, f"failed to delete model: {name}")
    return {"deleted": name}


class ReorientRequest(BaseModel):
    transform: str


@router.post("/{name}/reorient")
def reorient_endpoint(name: str, req: ReorientRequest):
    """Apply an in-place orientation transform to a stored model's .ply.

    `transform` is one of coord_convert.TRANSFORM_NAMES: axis rotations in
    Blender-style 90/180 degree steps, plus back-compatible shortcuts such as
    "y_up_to_z_up". Rewrites positions + gaussian quaternions + normals;
    everything else passes through. Repeatable — no lock — so apply, eyeball,
    apply again until it is upright. The response is the updated model meta,
    including a fresh `sha256` the client uses to cache-bust the splat fetch
    (the .ply is overwritten at the same path)."""
    if not Model.exists(name):
        raise HTTPException(404, f"model not found: {name}")
    try:
        return m.reorient_model(name, req.transform)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
