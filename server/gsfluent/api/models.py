from fastapi import APIRouter, File, HTTPException, UploadFile

from ..core import models as m

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
def list_endpoint():
    return m.list_models()


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".ply"):
        raise HTTPException(422, "only .ply uploads are accepted")
    content = await file.read()
    if len(content) < 64:
        raise HTTPException(422, "uploaded file is too small to be a valid ply")
    name, path = m.wrap_ply_upload(file.filename, content)
    return {"name": name, "path": str(path)}
