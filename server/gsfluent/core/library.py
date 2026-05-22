"""On-disk library: typed access to `work/library/{models,sequences}/`.

This module is the single source of truth for the new layout introduced by
the 2026-05-09 sequence-workflow spec. Both endpoints (api/models.py,
api/runs.py) and the migration script (server/tools/migrate_to_library.py) go
through `Model` / `Sequence` rather than poking the filesystem directly.

Layout (all paths Z-up by convention; conversion happens at import time):

    work/library/
    ├── models/<name>/
    │   ├── point_cloud/iteration_<N>/point_cloud.ply   # required
    │   ├── cameras.json                                # optional
    │   └── _meta.json                                  # required
    ├── models/_registered.json                         # external paths
    └── sequences/<name>/
        ├── frames/frame_NNNN.ply                       # required
        ├── _meta.json                                  # required
        └── recipe.json                                 # if source=sim

`_meta.json` is read tolerantly (missing/corrupt -> None + warning); written
atomically (tmp + replace) so a partial write can't poison the library.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from ..server import PKG_ROOT

_log = logging.getLogger(__name__)

LIBRARY_ROOT = PKG_ROOT / "work" / "library"
MODELS_DIR = LIBRARY_ROOT / "models"
SEQUENCES_DIR = LIBRARY_ROOT / "sequences"

# Path to the small JSON index that tracks externally-registered models
# (i.e. paths NOT physically inside MODELS_DIR — the user has a 3DGS dir
# elsewhere on disk and just wants it visible in the library without copy).
_REGISTERED_INDEX = MODELS_DIR / "_registered.json"

_ITER_RE = re.compile(r"^iteration_(\d+)$")
_FRAME_RE = re.compile(r"^frame_(\d+)\.ply$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


from .library_io import (  # noqa: E402 (re-exports kept below to avoid cycles)
    atomic_write_json as _atomic_write_json,
)
from .library_io import (  # noqa: E402
    read_json_tolerant as _read_json_tolerant,
)
from .library_io import (  # noqa: E402
    read_ply_bbox_and_count as _read_ply_bbox_and_count,
)

# Keep the legacy private alias so internal call sites that used the
# meta-flavored name continue to work without churn.
_read_meta_tolerant = _read_json_tolerant


def _check_coord(meta: dict, where: str) -> None:
    """Validate `coord_convention == "z-up"`. Logs a warning if not, but
    never rejects — migration tolerance: existing data may lack the
    field; we treat absent-or-not-z-up as a soft signal, not a hard fail.
    """
    cc = meta.get("coord_convention")
    if cc is not None and cc != "z-up":
        _log.warning("%s has coord_convention=%r (expected z-up)", where, cc)


# --- Pydantic shapes --------------------------------------------------------
#
# We use pydantic for validation only at the meta-write boundary. Internally
# `Model` / `Sequence` are thin path+meta wrappers — the dataclass-style
# instances the spec asks for. Pydantic gives us field-name validation
# without forcing every consumer of `meta_dict()` to learn a new type.


class _ModelMeta(BaseModel):
    name: str
    kind: str = Field(default="model")
    source: str  # "upload" | "register" | "import"
    source_path: str | None = None
    n_splats: int | None = None
    bbox: list[list[float]] | None = None  # [[xmin,ymin,zmin],[xmax,ymax,zmax]]
    coord_convention: str = Field(default="z-up")
    imported_at: str | None = None
    # Phase 4: Y-up source rewritten at import. Audit-only; downstream
    # code reads `coord_convention` (which is always "z-up") for
    # routing decisions.
    converted_from: str | None = None  # "y-up" | None
    # Content-hash of the originally uploaded ply bytes (pre-conversion).
    # Lets the upload endpoint skip transport on re-drops via
    # /api/models/check_hash. Legacy uploads pre-dedup carry None — the
    # first re-drop misses the cache, hits the upload path, and the new
    # meta written includes the hash so subsequent drops do skip.
    sha256: str | None = None


class _SequenceMeta(BaseModel):
    name: str
    kind: str = Field(default="sequence")
    source: str  # "sim" | "import"
    source_path: str | None = None
    model_ref: str | None = None
    frame_count: int = 0
    fps_hint: int = 24
    n_splats: int | None = None
    bbox_initial: list[list[float]] | None = None
    coord_convention: str = Field(default="z-up")
    first_frame_full: bool = True
    created_at: str | None = None
    # Phase 4: when set, frames in this sequence were rewritten from
    # the labelled axis convention into z-up at import time. Audit-only;
    # display/playback code should never branch on this.
    converted_from: str | None = None  # "y-up" | None


# --- Registered-externals index --------------------------------------------


def _load_registered() -> list[dict]:
    """Returns the list of registered external models (name, path).

    Tolerant of a missing/corrupt index — that's user data we should never
    crash on. Also drops legacy non-dict entries silently.
    """
    if not _REGISTERED_INDEX.is_file():
        return []
    try:
        data = json.loads(_REGISTERED_INDEX.read_text())
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("could not parse registered index: %s", e)
        return []
    if not isinstance(data, list):
        return []
    return [
        x for x in data
        if isinstance(x, dict) and "name" in x and "path" in x
    ]


def _save_registered(items: list[dict]) -> None:
    _atomic_write_json(_REGISTERED_INDEX, items)


def register_external(name: str, path: Path) -> None:
    """Add (or refresh) an external model entry in the registered index."""
    items = [x for x in _load_registered() if x.get("name") != name]
    items.insert(0, {"name": name, "path": str(path)})
    _save_registered(items)


def unregister_external(name: str) -> bool:
    items = _load_registered()
    new = [x for x in items if x.get("name") != name]
    if len(new) == len(items):
        return False
    _save_registered(new)
    return True


def get_registered_path(name: str) -> Path | None:
    """Resolve a registered-external name to its on-disk path, or None."""
    for x in _load_registered():
        if x.get("name") == name:
            try:
                return Path(x["path"])
            except Exception:
                return None
    return None


# --- Model -----------------------------------------------------------------


class Model:
    """A 3DGS model directory backed by `work/library/models/<name>/` (or
    an external registered path). Thin wrapper: holds `name`, resolved
    `path`, and `meta` (None if not yet written)."""

    KIND: ClassVar[str] = "model"

    def __init__(self, name: str, path: Path, meta: dict | None = None):
        self.name = name
        self.path = path
        self.meta = meta

    # ---- discovery / construction ----

    @classmethod
    def _resolve_path(cls, name: str) -> Path:
        """Map a name to its on-disk path. Internal layout wins over the
        registered-externals index, so a name collision falls back to the
        in-library copy (which the user has explicit control over)."""
        local = MODELS_DIR / name
        if local.is_dir():
            return local
        ext = get_registered_path(name)
        if ext is not None and ext.is_dir():
            return ext
        # Default to the local path even if it doesn't exist yet — callers
        # that need existence should ask `Model.exists(name)` first.
        return local

    @classmethod
    def exists(cls, name: str) -> bool:
        local = MODELS_DIR / name
        if local.is_dir():
            return True
        ext = get_registered_path(name)
        return ext is not None and ext.is_dir()

    @classmethod
    def load(cls, name: str) -> Model | None:
        if not cls.exists(name):
            return None
        path = cls._resolve_path(name)
        meta = _read_meta_tolerant(cls._meta_path_for(path))
        if meta is not None:
            _check_coord(meta, f"model {name}")
        return cls(name=name, path=path, meta=meta)

    @classmethod
    def list(cls) -> list[str]:
        """Return all known model names (internal + registered-external),
        de-duplicated, internal copies preferred. Sorted lexicographically.
        """
        names: set[str] = set()
        if MODELS_DIR.is_dir():
            for child in MODELS_DIR.iterdir():
                if child.is_dir() and not child.name.startswith("_"):
                    names.add(child.name)
        for entry in _load_registered():
            n = entry.get("name")
            p = entry.get("path")
            if not n or not p:
                continue
            try:
                if Path(p).is_dir():
                    names.add(n)
            except Exception:
                continue
        return sorted(names)

    @classmethod
    def find_by_hash(cls, sha256: str) -> Model | None:
        """Scan library models for one whose meta carries this sha256.

        Returns the first match (there should only be one). Returns None if
        no model in the library has this hash. Skips models with no sha256
        (legacy uploads before dedup landed).
        """
        if not sha256:
            return None
        for name in cls.list():
            m = cls.load(name)
            if m is None or m.meta is None:
                continue
            if m.meta.get("sha256") == sha256:
                return m
        return None

    # ---- meta IO ----

    @staticmethod
    def _meta_path_for(model_dir: Path) -> Path:
        """Return the meta file path for a model dir.

        Inside the library we always write `_meta.json`. For an external
        registered dir, we still prefer `_meta.json` at the root — but if
        that fails (read-only mount), we fall back to `.gsfluent_meta.json`
        as a sidecar. Read-side accepts either.
        """
        return model_dir / "_meta.json"

    @classmethod
    def write_meta(
        cls,
        name: str,
        *,
        source: str,
        source_path: str | None = None,
        n_splats: int | None = None,
        bbox: list[list[float]] | None = None,
        coord_convention: str = "z-up",
        imported_at: str | None = None,
        path: Path | None = None,
        converted_from: str | None = None,
        sha256: str | None = None,
    ) -> Path:
        """Write `_meta.json` for the model.

        For registered externals, pass `path=` explicitly. The function
        attempts the main meta path first; on PermissionError/OSError it
        falls back to a sidecar (`<external>/.gsfluent_meta.json`).
        Returns the path that was actually written.
        """
        target_dir = path if path is not None else MODELS_DIR / name
        payload = _ModelMeta(
            name=name,
            kind="model",
            source=source,
            source_path=source_path,
            n_splats=n_splats,
            bbox=bbox,
            coord_convention=coord_convention,
            imported_at=imported_at or _now_iso(),
            converted_from=converted_from,
            sha256=sha256,
        ).model_dump()
        primary = cls._meta_path_for(target_dir)
        try:
            _atomic_write_json(primary, payload)
            return primary
        except (PermissionError, OSError) as e:
            sidecar = target_dir / ".gsfluent_meta.json"
            _log.warning(
                "could not write %s (%s); falling back to sidecar %s",
                primary, e, sidecar,
            )
            _atomic_write_json(sidecar, payload)
            return sidecar

    def meta_dict(self) -> dict:
        """Frontend-facing dict. Always contains at least `name` + `path`
        so the existing `ModelItem` type contract holds. Falls back to
        synthesized fields if the on-disk meta is missing."""
        if self.meta is not None:
            d = dict(self.meta)
        else:
            d = {"name": self.name, "kind": self.KIND, "source": "unknown"}
        d.setdefault("name", self.name)
        d["path"] = str(self.path)
        return d

    # ---- delete ----

    @classmethod
    def delete(cls, name: str, *, unlink_only_if_registered: bool = True) -> bool:
        """Remove a model from the library.

        If the model is a registered external (path lives outside MODELS_DIR
        and is in `_registered.json`), and `unlink_only_if_registered=True`,
        we only drop the registry entry — never touch user files outside
        our library root. For internal copies we shutil.rmtree the dir.
        """
        if not cls.exists(name):
            return False
        local = MODELS_DIR / name
        is_internal = local.is_dir()
        if is_internal:
            try:
                shutil.rmtree(local)
            except OSError as e:
                _log.warning("failed to delete model dir %s: %s", local, e)
                return False
            unregister_external(name)
            return True
        # External — only drop the registry entry; never delete user files.
        if unlink_only_if_registered:
            return unregister_external(name)
        return unregister_external(name)

    # ---- ply discovery ----

    def highest_iteration_ply(self) -> Path | None:
        """Locate `<path>/point_cloud/iteration_<N>/point_cloud.ply` with
        the highest N. Returns None if no candidate exists."""
        pc_root = self.path / "point_cloud"
        if not pc_root.is_dir():
            return None
        best: tuple[int, Path] | None = None
        for it in pc_root.iterdir():
            if not it.is_dir():
                continue
            m = _ITER_RE.match(it.name)
            if not m:
                continue
            ply = it / "point_cloud.ply"
            if ply.is_file():
                n = int(m.group(1))
                if best is None or n > best[0]:
                    best = (n, ply)
        return None if best is None else best[1]


# --- Sequence --------------------------------------------------------------


class Sequence:
    """A time-sampled .ply collection backed by
    `work/library/sequences/<name>/frames/`."""

    KIND: ClassVar[str] = "sequence"

    def __init__(self, name: str, path: Path, meta: dict | None = None):
        self.name = name
        self.path = path
        self.meta = meta

    # ---- discovery ----

    @classmethod
    def exists(cls, name: str) -> bool:
        return (SEQUENCES_DIR / name).is_dir()

    @classmethod
    def load(cls, name: str) -> Sequence | None:
        if not cls.exists(name):
            return None
        path = SEQUENCES_DIR / name
        meta = _read_meta_tolerant(path / "_meta.json")
        if meta is not None:
            _check_coord(meta, f"sequence {name}")
        return cls(name=name, path=path, meta=meta)

    @classmethod
    def list(cls) -> list[str]:
        if not SEQUENCES_DIR.is_dir():
            return []
        out: list[str] = []
        for child in SEQUENCES_DIR.iterdir():
            if child.is_dir() and not child.name.startswith("_"):
                out.append(child.name)
        return sorted(out)

    # ---- meta IO ----

    @classmethod
    def write_meta(
        cls,
        name: str,
        *,
        source: str,
        source_path: str | None = None,
        model_ref: str | None = None,
        frame_count: int = 0,
        fps_hint: int = 24,
        n_splats: int | None = None,
        bbox_initial: list[list[float]] | None = None,
        coord_convention: str = "z-up",
        first_frame_full: bool = True,
        created_at: str | None = None,
        converted_from: str | None = None,
    ) -> Path:
        target_dir = SEQUENCES_DIR / name
        payload = _SequenceMeta(
            name=name,
            kind="sequence",
            source=source,
            source_path=source_path,
            model_ref=model_ref,
            frame_count=frame_count,
            fps_hint=fps_hint,
            n_splats=n_splats,
            bbox_initial=bbox_initial,
            coord_convention=coord_convention,
            first_frame_full=first_frame_full,
            created_at=created_at or _now_iso(),
            converted_from=converted_from,
        ).model_dump()
        meta_path = target_dir / "_meta.json"
        _atomic_write_json(meta_path, payload)
        return meta_path

    def meta_dict(self) -> dict:
        if self.meta is not None:
            d = dict(self.meta)
        else:
            d = {"name": self.name, "kind": self.KIND, "source": "unknown"}
        d.setdefault("name", self.name)
        d["path"] = str(self.path)
        return d

    # ---- frames ----

    def frames_dir(self) -> Path:
        return self.path / "frames"

    def frame_paths(self) -> list[Path]:
        """Sorted list of frame_NNNN.ply paths. Sort key is the integer
        frame index parsed from the filename — lexicographic order would
        put frame_10 before frame_2 and break playback."""
        d = self.frames_dir()
        if not d.is_dir():
            return []
        out: list[tuple[int, Path]] = []
        for p in d.iterdir():
            if not p.is_file():
                continue
            m = _FRAME_RE.match(p.name)
            if m:
                out.append((int(m.group(1)), p))
        out.sort(key=lambda t: t[0])
        return [p for _, p in out]

    def frame_count(self) -> int:
        return len(self.frame_paths())

    def is_live(self) -> bool:
        """True iff a writer process is still appending frames.

        Phase 1: always returns False. Live-sim wiring (the runner-driven
        per-sequence flag) is Phase 2's responsibility.
        """
        return False

    @property
    def is_broken(self) -> bool:
        """True iff `frames/` is a symlink whose target no longer exists.

        Sim-produced sequences (where `frames/` is a real directory) are
        never broken — only symlinked imports become broken when the user
        moves or deletes the source folder.
        """
        frames = self.path / "frames"
        if not frames.is_symlink():
            return False
        try:
            return not frames.resolve(strict=False).exists()
        except OSError:
            return True

    # ---- delete ----

    @classmethod
    def delete(cls, name: str) -> bool:
        if not cls.exists(name):
            return False
        target = SEQUENCES_DIR / name
        try:
            shutil.rmtree(target)
        except OSError as e:
            _log.warning("failed to delete sequence dir %s: %s", target, e)
            return False
        return True


# --- helpers shared with migration / endpoints ------------------------------


# Required attribute set for a "full" 3DGS .ply (positions live in
# `vertex.x/y/z` always; we additionally demand SH DC, scales, rotations,
# and opacity). xyz-only frames (sim per-frame position updates) lack
# these, so this set distinguishes "first frame is a real model" from
# "first frame is a position-only delta".
_FULL_3DGS_ATTRS = (
    "f_dc_0", "f_dc_1", "f_dc_2",
    "scale_0", "scale_1", "scale_2",
    "rot_0", "rot_1", "rot_2", "rot_3",
    "opacity",
)


def _is_full_3dgs_ply(ply_path: Path) -> tuple[bool, list[str]]:
    """Return (is_full, missing_attrs).

    A full 3DGS .ply has positions plus SH DC + scales + rotations + opacity.
    On read failure returns (False, ["<error>"]) — caller treats as not-full.
    """
    try:
        from plyfile import PlyData
        v = PlyData.read(str(ply_path))["vertex"]
        names = {p.name for p in v.properties}
        missing = [a for a in _FULL_3DGS_ATTRS if a not in names]
        return (len(missing) == 0, missing)
    except Exception as e:
        return (False, [f"<read error: {e}>"])


def import_sequence(
    folder_path: Path,
    name: str | None = None,
    convert_y_up: bool = False,
) -> Sequence:
    """Register an external folder of `frame_*.ply` as a Sequence.

    Symlinks `<library>/sequences/<name>/frames` -> `folder_path`. No frames
    are copied — the source folder remains the source of truth. If it moves
    or is deleted, `Sequence.load()` still reads `_meta.json` but
    `frame_paths()` returns `[]`; callers should treat this as "broken
    source" (see `Sequence.is_broken`).

    Validation:
      - `folder_path` must be an existing directory
      - must contain at least one `frame_*.ply`
      - the lowest-numeric `frame_*.ply` must be a full 3DGS .ply
        (positions + SH DC + scales + rotations + opacity)
      - `frame_count` is the number of matching files, regardless of gaps

    `name` defaults to `folder_path.name`. If a sequence with that name
    already exists, raises `FileExistsError` — caller can pass an explicit
    `name` for the rename case.

    `convert_y_up=True`: instead of symlinking, frames are MATERIALIZED
    into `<library>/sequences/<name>/frames/`, with each frame rewritten
    Y-up -> Z-up via `core.coord_convert.convert_full_3dgs_ply`. Costs
    disk space (a full copy of the source) but the library entry is
    self-contained -- no danger of going `is_broken` if the user moves
    the source folder. `_meta.json:source_path` still records the
    original input for audit; `_meta.json:converted_from = "y-up"`
    flags the rewrite. The default (False) keeps the symlink semantics
    untouched.
    """
    folder_path = Path(folder_path)
    if not folder_path.exists():
        raise FileNotFoundError(f"folder does not exist: {folder_path}")
    if not folder_path.is_dir():
        raise NotADirectoryError(f"not a directory: {folder_path}")

    # Collect frame files, sorted by integer index parsed from the name.
    frames: list[tuple[int, Path]] = []
    for p in folder_path.iterdir():
        if not p.is_file():
            continue
        m = _FRAME_RE.match(p.name)
        if m:
            frames.append((int(m.group(1)), p))
    if not frames:
        raise ImportError(
            f"no frame_*.ply files found in {folder_path}"
        )
    frames.sort(key=lambda t: t[0])
    frame0 = frames[0][1]

    # Validate frame 0 is a full 3DGS ply.
    is_full, missing = _is_full_3dgs_ply(frame0)
    if not is_full:
        raise ImportError(
            f"frame 0 ({frame0.name}) is not a full 3DGS .ply: missing "
            f"attrs {missing}"
        )

    seq_name = name if name is not None else folder_path.name
    if not seq_name or seq_name.startswith(".") or "/" in seq_name:
        raise ValueError(f"invalid sequence name: {seq_name!r}")

    # Refuse to clobber an existing sequence — the caller must decide
    # whether to delete-then-reimport or pick a fresh name.
    if Sequence.exists(seq_name):
        raise FileExistsError(
            f"sequence already exists: {seq_name}"
        )

    seq_dir = SEQUENCES_DIR / seq_name
    seq_dir.mkdir(parents=True)

    if convert_y_up:
        # Materialize converted frames into seq_dir/frames/ — this is
        # the only path that physically copies bytes into the library.
        # Rolls back the half-built dir on any failure mid-conversion
        # so retries don't leave a partial sequence behind.
        from .coord_convert import convert_full_3dgs_ply

        frames_out = seq_dir / "frames"
        frames_out.mkdir()
        try:
            for _idx, src in frames:
                dst = frames_out / src.name
                convert_full_3dgs_ply(src, dst)
        except Exception:
            try:
                shutil.rmtree(seq_dir)
            except OSError:
                pass
            raise
        # Read bbox/count from the CONVERTED frame 0 so meta reflects
        # the on-disk Z-up data, not the original Y-up source.
        converted_frame0 = frames_out / frame0.name
        n_splats, bbox = read_ply_bbox_and_count(converted_frame0)
        meta_converted_from: str | None = "y-up"
    else:
        # Symlink frames/ -> source folder. No fallback to copy — explicit
        # error is better than a 100GB silent copy on Windows-without-admin.
        frames_link = seq_dir / "frames"
        try:
            os.symlink(
                str(folder_path.resolve()),
                str(frames_link),
                target_is_directory=True,
            )
        except OSError as e:
            # Roll back the empty seq_dir so a retry with a different name
            # doesn't leave half-built dirs behind.
            try:
                shutil.rmtree(seq_dir)
            except OSError:
                pass
            raise OSError(
                f"failed to create symlink {frames_link} -> {folder_path}: {e}"
            ) from e
        n_splats, bbox = read_ply_bbox_and_count(frame0)
        meta_converted_from = None

    Sequence.write_meta(
        name=seq_name,
        source="import",
        source_path=str(folder_path.resolve()),
        model_ref=None,
        frame_count=len(frames),
        fps_hint=24,
        n_splats=n_splats,
        bbox_initial=bbox,
        coord_convention="z-up",
        first_frame_full=True,
        created_at=_now_iso(),
        converted_from=meta_converted_from,
    )

    seq = Sequence.load(seq_name)
    if seq is None:  # pragma: no cover — write_meta + dir creation succeeded
        raise RuntimeError(f"failed to load freshly-imported sequence {seq_name}")
    return seq


# Public re-export so external callers (e.g. core/runner.py) continue
# to find `library.read_ply_bbox_and_count`.
read_ply_bbox_and_count = _read_ply_bbox_and_count
