"""Shared filesystem primitives used by core/library.py and storage/filesystem.py.

These were originally private helpers inside core/library.py. They moved here
so the FilesystemStorage impl (storage/filesystem.py) can use them without
depending on the Model/Sequence business types.

All write helpers are atomic on the same filesystem (tmp + os.replace).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)


def atomic_write_json(path: Path, payload: dict) -> None:
    """Write `payload` as pretty-printed JSON via tmp + os.replace.

    Atomic on the same filesystem; safe against partial writes that would
    leave a half-written file readable mid-flight.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write `payload` (raw bytes) atomically via tmp + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def read_json_tolerant(path: Path) -> dict | None:
    """Read a JSON file returning a dict, or None on missing/corrupt/non-dict.

    Tolerance is the point: callers iterate over many entries, and a single
    bad file shouldn't crash the listing — just log and skip.
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        _log.warning("could not parse JSON at %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        _log.warning("JSON at %s is not an object", path)
        return None
    return data


def read_ply_bbox_and_count(
    ply_path: Path,
) -> tuple[int | None, list[list[float]] | None]:
    """Read a .ply and return (n_splats, bbox). Tolerant of unreadable files —
    returns (None, None) on any failure so callers can degrade gracefully.
    """
    try:
        import numpy as np
        from plyfile import PlyData

        v = PlyData.read(str(ply_path))["vertex"].data
        n = int(v.shape[0])
        x = np.asarray(v["x"], dtype=np.float64)
        y = np.asarray(v["y"], dtype=np.float64)
        z = np.asarray(v["z"], dtype=np.float64)
        if n == 0:
            return n, None
        bbox = [
            [float(x.min()), float(y.min()), float(z.min())],
            [float(x.max()), float(y.max()), float(z.max())],
        ]
        return n, bbox
    except Exception as e:
        _log.warning("could not read bbox/count from %s: %s", ply_path, e)
        return None, None
