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


def wrap_ply_upload(
    orig_filename: str,
    content: bytes,
    cameras_json_bytes: bytes | None = None,
) -> tuple[str, Path]:
    """Wrap a raw .ply upload into the 3DGS directory layout the sim expects.

    If `cameras_json_bytes` is supplied, it's written verbatim as the
    model's cameras.json (e.g. the original COLMAP output). Otherwise a
    minimal synthetic one is generated from the ply's bbox.

    Returns (model_name, model_dir_path)."""
    base = Path(orig_filename).stem or "model"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    if not safe:
        safe = "model"
    name = f"{safe}_{uuid.uuid4().hex[:8]}"
    iter_dir = UPLOADS_DIR / name / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True, exist_ok=True)
    ply_path = iter_dir / "point_cloud.ply"
    ply_path.write_bytes(content)  # TODO Phase 4: write to .tmp + replace for atomic .ply landing
    model_dir = UPLOADS_DIR / name
    # The sim core (utils/camera_view_utils.py:get_camera_view) opens
    # `<model_dir>/cameras.json` unconditionally. Prefer the real file if
    # the uploader provided one; otherwise synthesize from the ply bbox.
    if cameras_json_bytes is not None:
        try:
            (model_dir / "cameras.json").write_bytes(cameras_json_bytes)
        except Exception as e:
            _log.warning("could not write uploaded cameras.json for %s: %s", name, e)
    else:
        try:
            _ensure_cameras_json(model_dir, ply_path)
        except Exception as e:
            _log.warning("could not generate cameras.json for %s: %s", name, e)
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
    # Sim core needs cameras.json. If the user's external dir doesn't have
    # one, write a synthetic one in-place. We pick the highest-iteration
    # ply for the bbox.
    if not (path / "cameras.json").exists():
        best_ply = None
        best_iter = -1
        for it in pc_root.glob("iteration_*"):
            ply = it / "point_cloud.ply"
            if not ply.is_file():
                continue
            try:
                n = int(it.name.split("_")[1])
            except (IndexError, ValueError):
                continue
            if n > best_iter:
                best_iter, best_ply = n, ply
        if best_ply is not None:
            try:
                _ensure_cameras_json(path, best_ply)
            except Exception as e:
                _log.warning("could not generate cameras.json for %s: %s", path, e)
    record_model(name, path)
    return name, path


def _ensure_cameras_json(model_dir: Path, ply_path: Path) -> None:
    """Write a minimal `<model_dir>/cameras.json` if it doesn't already exist.

    The file must be valid JSON parseable as `list[dict]` with each entry
    containing at least: id, img_name, width, height, position, rotation,
    fx, fy. We synthesize a single camera positioned 2× the bbox diagonal
    from the bbox center along (1, 0, 1), looking at the center.
    """
    cam_path = model_dir / "cameras.json"
    if cam_path.exists():
        return

    import math
    from plyfile import PlyData
    import numpy as np

    v = PlyData.read(str(ply_path))["vertex"].data
    x = np.asarray(v["x"], dtype=np.float64)
    y = np.asarray(v["y"], dtype=np.float64)
    z = np.asarray(v["z"], dtype=np.float64)
    cx, cy, cz = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2, (z.min() + z.max()) / 2
    dx_, dy_, dz_ = x.max() - x.min(), y.max() - y.min(), z.max() - z.min()
    diag = max(math.sqrt(dx_ * dx_ + dy_ * dy_ + dz_ * dz_), 1.0)

    cam_pos = [cx + diag, cy, cz + diag]
    forward = np.array([cx - cam_pos[0], cy - cam_pos[1], cz - cam_pos[2]], dtype=np.float64)
    forward /= np.linalg.norm(forward)
    up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-6:
        up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    new_up = np.cross(right, forward)
    rotation = [
        [float(right[0]), float(right[1]), float(right[2])],
        [float(new_up[0]), float(new_up[1]), float(new_up[2])],
        [float(-forward[0]), float(-forward[1]), float(-forward[2])],
    ]
    cam_path.write_text(json.dumps([{
        "id": 0,
        "img_name": "synthetic_0001",
        "width": 800,
        "height": 800,
        "position": [float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])],
        "rotation": rotation,
        "fx": 1111.111,
        "fy": 1111.111,
    }], indent=2))
