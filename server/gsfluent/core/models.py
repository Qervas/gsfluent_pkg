"""Model uploads + history.

A "model" is a 3DGS scan. The sim core expects layout
`<dir>/point_cloud/iteration_<N>/point_cloud.ply`. We auto-wrap raw .ply
uploads into that layout so the rest of the pipeline doesn't need to
know about uploads vs externally-trained models.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from ..server import PKG_ROOT

_log = logging.getLogger(__name__)

UPLOADS_DIR = PKG_ROOT / "work" / "uploads"
HISTORY_FILE = PKG_ROOT / "work" / "_state" / "model_history.json"
MAX_HISTORY = 20


def _load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    # Filter out legacy non-dict entries — earlier versions wrote a list of
    # path strings, which would crash record_model's .get() filter.
    return [x for x in data if isinstance(x, dict) and "name" in x and "path" in x]


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
    (iter_dir / "point_cloud.ply").write_bytes(content)  # TODO Phase 4: write to .tmp + replace for atomic .ply landing
    model_dir = UPLOADS_DIR / name
    # History is a hint, not source of truth: a write here failing must NOT
    # block the upload. We log and move on; the next listings refresh will
    # pick up whatever is on disk.
    try:
        record_model(name, model_dir)
    except Exception as e:
        _log.warning("model %s saved to %s but history update failed: %s", name, model_dir, e)
    return name, model_dir


def register_local_model(path: Path) -> tuple[str, Path]:
    """Register an existing local 3DGS model directory in history without
    copying anything. Validates the path structure: must contain
    `point_cloud/iteration_*/point_cloud.ply`. Returns (name, path).

    Raises FileNotFoundError if the path doesn't exist or doesn't have
    the expected layout.
    """
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"path does not exist or is not a directory: {path}")
    pc_root = path / "point_cloud"
    if not pc_root.is_dir():
        raise FileNotFoundError(f"missing point_cloud/ subdir under {path}")
    # Look for any iteration_<N>/point_cloud.ply.
    iters = sorted(pc_root.glob("iteration_*"))
    if not iters:
        raise FileNotFoundError(
            f"no iteration_*/ subdir under {pc_root}. "
            f"3DGS model layout requires <model>/point_cloud/iteration_<N>/point_cloud.ply"
        )
    # At least one iteration must have a point_cloud.ply.
    if not any((it / "point_cloud.ply").is_file() for it in iters):
        raise FileNotFoundError(
            f"no point_cloud.ply found under {pc_root}/iteration_*/. "
            f"Are you pointing at a 3DGS training output dir?"
        )
    name = path.name  # use the directory name verbatim — no uuid suffix needed
    record_model(name, path)
    return name, path
