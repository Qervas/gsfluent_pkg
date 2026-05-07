"""Model uploads + history.

A "model" is a 3DGS scan. The sim core expects layout
`<dir>/point_cloud/iteration_<N>/point_cloud.ply`. We auto-wrap raw .ply
uploads into that layout so the rest of the pipeline doesn't need to
know about uploads vs externally-trained models.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..server import PKG_ROOT

UPLOADS_DIR = PKG_ROOT / "work" / "uploads"
HISTORY_FILE = PKG_ROOT / "work" / "_state" / "model_history.json"
MAX_HISTORY = 20


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text())
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_history(items: list[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(items, indent=2))
        tmp.replace(HISTORY_FILE)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def list_models() -> list[dict]:
    return _load_history()


def record_model(name: str, path: Path) -> None:
    items = [x for x in _load_history() if x.get("name") != name]
    items.insert(0, {"name": name, "path": str(path)})
    _save_history(items[:MAX_HISTORY])


def wrap_ply_upload(orig_filename: str, content: bytes) -> tuple[str, Path]:
    """Wrap a raw .ply upload into the 3DGS directory layout the sim expects.

    Returns (model_name, model_dir_path)."""
    base = Path(orig_filename).stem or "model"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    if not safe:
        safe = "model"
    name = f"{safe}_{uuid.uuid4().hex[:8]}"
    iter_dir = UPLOADS_DIR / name / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "point_cloud.ply").write_bytes(content)
    model_dir = UPLOADS_DIR / name
    record_model(name, model_dir)
    return name, model_dir
