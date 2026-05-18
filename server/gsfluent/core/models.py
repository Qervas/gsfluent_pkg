"""Model uploads + listing, library-backed.

A "model" is a 3DGS scan. The sim core expects the canonical layout
`<dir>/point_cloud/iteration_<N>/point_cloud.ply`. We auto-wrap raw .ply
uploads into that layout and write a `_meta.json` so the rest of the
pipeline (and future spec phases) doesn't need to know about uploads vs
externally-trained models.

The on-disk root is `work/library/models/<name>/` (per the 2026-05-09
sequence-workflow spec). External `register_local_model` paths stay where
they are on disk and get tracked through `library._registered.json`.

Backwards-compatible API:
  - `list_models() -> list[dict]` still returns at least `name` + `path`
    for each entry, so the frontend's `ModelItem` contract holds.
  - `wrap_ply_upload`, `register_local_model` keep their signatures.
  - `UPLOADS_DIR`, `HISTORY_FILE`, `MAX_HISTORY` symbols are kept as
    legacy aliases for tests that monkeypatch them; new code should
    import from `core.library` instead.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

# Allowlist regex for any name that becomes part of a filesystem path
# (model dirs under MODELS_DIR, registered-index entries, etc.).
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

from . import library as lib
from ..server import PKG_ROOT
from .library import MODELS_DIR, Model, read_ply_bbox_and_count

_log = logging.getLogger(__name__)

# Legacy aliases — pre-Phase-1 callers/tests poked these. Kept so the
# external contract doesn't change in this commit; future phases can
# switch consumers to `library.MODELS_DIR` etc directly.
UPLOADS_DIR = MODELS_DIR
HISTORY_FILE = lib._REGISTERED_INDEX  # only used by external-register path
MAX_HISTORY = 20  # unused under library-backed list, but kept for compat


def list_models() -> list[dict]:
    """Return all models in the library (internal + registered external).

    Each entry carries at minimum `name` and `path`, plus whatever fields
    the model's `_meta.json` defined. Sorted by `imported_at` desc (ISO-8601
    UTC timestamps sort lexicographically into chronological order); entries
    without `imported_at` sort last, alphabetically.
    """
    out: list[dict] = []
    for name in Model.list():
        m = Model.load(name)
        if m is None:
            continue
        out.append(m.meta_dict())
    with_ts = [d for d in out if d.get("imported_at")]
    no_ts = [d for d in out if not d.get("imported_at")]
    with_ts.sort(key=lambda d: d.get("imported_at", ""), reverse=True)
    no_ts.sort(key=lambda d: d.get("name", ""))
    return with_ts + no_ts


def wrap_ply_upload(
    orig_filename: str,
    content: bytes,
    cameras_json_bytes: bytes | None = None,
    convert_y_up: bool = False,
    sha256: str | None = None,
) -> tuple[str, Path]:
    """Wrap a raw .ply upload into the 3DGS directory layout the sim expects.

    If `cameras_json_bytes` is supplied, it's written verbatim as the
    model's cameras.json (e.g. the original COLMAP output). Otherwise a
    minimal synthetic one is generated from the ply's bbox.

    Writes `_meta.json` alongside (kind=model, source=upload, n_splats and
    bbox derived from the ply, coord_convention=z-up).

    `convert_y_up=True`: bytes are written to a tmp path, then rewritten
    Y-up -> Z-up (positions, per-gaussian quaternions, normals) into the
    final point_cloud.ply via `core.coord_convert.convert_full_3dgs_ply`.
    The synthetic cameras.json is generated AFTER conversion so its
    bbox reflects the converted ply. Sets `_meta.json:converted_from =
    "y-up"` for audit.

    `sha256` (when supplied) is recorded into `_meta.json:sha256`.
    Pre-computed by the caller so /api/models/check_hash can short-circuit
    re-uploads of identical content. If None, the meta records None —
    legacy/back-compat.

    Returns (model_name, model_dir_path).
    """
    base = Path(orig_filename).stem or "model"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in base)
    if not safe:
        safe = "model"
    name = f"{safe}_{uuid.uuid4().hex[:8]}"
    iter_dir = MODELS_DIR / name / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True, exist_ok=True)
    ply_path = iter_dir / "point_cloud.ply"

    if convert_y_up:
        # Write bytes to a sibling tmp path, then convert into the final
        # target ply. The converter does its own atomic tmp+replace on
        # the OUTPUT side, so we just need a stable read source and the
        # tmp deleted afterwards regardless of success/failure.
        from .coord_convert import convert_full_3dgs_ply

        src_tmp = iter_dir / "_yup_source.ply.tmp"
        src_tmp.write_bytes(content)
        try:
            convert_full_3dgs_ply(src_tmp, ply_path)
        finally:
            try:
                src_tmp.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        ply_path.write_bytes(content)  # TODO Phase 4: stream via tmp + replace for atomic landing on >1GB plys
    model_dir = MODELS_DIR / name

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

    # Read ply for n_splats / bbox. Tolerant of failures — the meta file
    # gets written either way; missing fields are harmless to consumers.
    n_splats, bbox = read_ply_bbox_and_count(ply_path)
    try:
        Model.write_meta(
            name=name,
            source="upload",
            source_path=None,
            n_splats=n_splats,
            bbox=bbox,
            coord_convention="z-up",
            converted_from="y-up" if convert_y_up else None,
            sha256=sha256,
        )
    except Exception as e:
        _log.warning("model %s wrote ply but _meta.json failed: %s", name, e)

    return name, model_dir


def register_local_model(
    path: Path, convert_y_up: bool = False,
) -> tuple[str, Path, str]:
    """Register an existing local 3DGS model directory in the library
    without copying anything. Validates the path structure (must contain
    `point_cloud/iteration_*/point_cloud.ply`), writes `_meta.json` AT
    that path (or in a sidecar `<external>/.gsfluent_meta.json` if the
    main meta path is read-only), and adds an entry to the registered
    index. Returns (name, path, mode).

    `mode` is "registered" by default, or "copied-and-converted" when
    `convert_y_up=True`: register's no-copy invariant breaks for
    converted models because we can't legally rewrite bytes inside the
    user's external directory. So the convert path materializes a fresh
    copy under `library/models/<name>/` (effectively switching to import
    semantics), and the API response surfaces the mode flag so the
    frontend can be honest about what happened.

    Raises FileNotFoundError if the path doesn't exist or doesn't have
    the expected layout.
    """
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"path does not exist or is not a directory: {path}")
    pc_root = path / "point_cloud"
    if not pc_root.is_dir():
        raise FileNotFoundError(f"missing point_cloud/ subdir under {path}")
    iters = sorted(pc_root.glob("iteration_*"))
    if not iters:
        raise FileNotFoundError(
            f"no iteration_*/ subdir under {pc_root}. "
            f"3DGS model layout requires <model>/point_cloud/iteration_<N>/point_cloud.ply"
        )
    if not any((it / "point_cloud.ply").is_file() for it in iters):
        raise FileNotFoundError(
            f"no point_cloud.ply found under {pc_root}/iteration_*/. "
            f"Are you pointing at a 3DGS training output dir?"
        )
    name = path.name  # use the directory name verbatim — no uuid suffix needed
    # The model name becomes part of every downstream filesystem path
    # (registered index, _meta.json under MODELS_DIR/<name>/, etc.).
    # Reject anything that's not a plain identifier so a user can't
    # register a model named "../../etc" and poison neighbouring dirs.
    if not _NAME_RE.match(name):
        raise ValueError(
            f"refusing to register model with unsafe directory name: {name!r} "
            f"(must match {_NAME_RE.pattern})"
        )

    # Convert-on-register branch: copy the entire structure into the
    # library, rewriting every iteration_*/point_cloud.ply Y-up -> Z-up.
    # Past this branch the function returns a different (mode, path)
    # contract; the caller (api/models.register) surfaces the mode.
    if convert_y_up:
        from .coord_convert import convert_full_3dgs_ply
        import shutil as _sh

        if MODELS_DIR.exists() and (MODELS_DIR / name).exists():
            raise FileExistsError(
                f"a model named {name!r} already exists in the library; "
                "delete it or rename your source dir to convert."
            )
        target = MODELS_DIR / name
        target.mkdir(parents=True)
        try:
            # Copy the cameras.json verbatim if present (camera intrinsics
            # are coord-system-agnostic in our pipeline; conversion of
            # poses would be a larger change we don't take on here).
            if (path / "cameras.json").is_file():
                _sh.copy2(path / "cameras.json", target / "cameras.json")
            for it in pc_root.glob("iteration_*"):
                src_ply = it / "point_cloud.ply"
                if not src_ply.is_file():
                    continue
                dst_iter = target / "point_cloud" / it.name
                dst_iter.mkdir(parents=True)
                convert_full_3dgs_ply(src_ply, dst_iter / "point_cloud.ply")
        except Exception:
            try:
                _sh.rmtree(target)
            except OSError:
                pass
            raise

        # Compute meta from the converted highest-iteration ply.
        n_splats = bbox = None
        m = Model(name=name, path=target)
        best = m.highest_iteration_ply()
        if best is not None:
            n_splats, bbox = read_ply_bbox_and_count(best)
            # Make sure cameras.json exists post-convert (synthetic if missing).
            if not (target / "cameras.json").exists():
                try:
                    _ensure_cameras_json(target, best)
                except Exception as e:
                    _log.warning("could not generate cameras.json for %s: %s", target, e)

        try:
            Model.write_meta(
                name=name,
                source="register",
                source_path=str(path),
                n_splats=n_splats,
                bbox=bbox,
                coord_convention="z-up",
                converted_from="y-up",
            )
        except Exception as e:
            _log.warning(
                "converted-register %s wrote ply but _meta.json failed: %s", name, e,
            )
        return name, target, "copied-and-converted"

    # Sim core needs cameras.json. If the user's external dir doesn't have
    # one, write a synthetic one in-place against the highest-iteration ply.
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

    # Compute meta from the highest-iteration ply we just identified.
    n_splats = bbox = None
    iter_re_match = sorted(
        ((int(it.name.split("_")[1]), it) for it in pc_root.glob("iteration_*")
         if (it / "point_cloud.ply").is_file()),
        key=lambda t: -t[0],
    )
    if iter_re_match:
        n_splats, bbox = read_ply_bbox_and_count(iter_re_match[0][1] / "point_cloud.ply")

    try:
        meta_path = Model.write_meta(
            name=name,
            source="register",
            source_path=str(path),
            n_splats=n_splats,
            bbox=bbox,
            coord_convention="z-up",
            path=path,
        )
        _log.info("registered model %s; meta at %s", name, meta_path)
    except Exception as e:
        _log.warning("registered model %s but _meta.json write failed: %s", name, e)

    # Track in the registered index so list_models() includes it even
    # though the dir lives outside MODELS_DIR.
    try:
        lib.register_external(name, path)
    except Exception as e:
        _log.warning("registered model %s but index update failed: %s", name, e)

    return name, path, "registered"


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
