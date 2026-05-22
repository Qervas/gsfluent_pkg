"""Tests for shared filesystem primitives used by library.py and storage/filesystem.py."""
import json
from pathlib import Path

import pytest

from gsfluent.core.library_io import (
    atomic_write_bytes,
    atomic_write_json,
    read_json_tolerant,
    read_ply_bbox_and_count,
)


def test_atomic_write_json_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_json(target, {"k": 1})
    assert json.loads(target.read_text()) == {"k": 1}


def test_atomic_write_json_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_text('{"old": true}')
    atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}


def test_atomic_write_json_via_temp_then_rename(tmp_path: Path) -> None:
    """The .tmp file should not remain after a successful write."""
    target = tmp_path / "out.json"
    atomic_write_json(target, {"k": 1})
    assert not (tmp_path / "out.json.tmp").exists()


def test_atomic_write_json_cleanup_on_failure(tmp_path: Path, monkeypatch) -> None:
    """If os.replace fails, the .tmp file should be removed."""
    import os
    target = tmp_path / "out.json"

    def boom(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(target, {"k": 1})
    assert not (tmp_path / "out.json.tmp").exists()


def test_atomic_write_bytes_writes_payload(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"hello\x00world")
    assert target.read_bytes() == b"hello\x00world"


def test_atomic_write_bytes_via_temp_then_rename(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"abc")
    assert not (tmp_path / "out.bin.tmp").exists()


def test_read_json_tolerant_returns_dict(tmp_path: Path) -> None:
    target = tmp_path / "meta.json"
    target.write_text('{"k": 2}')
    assert read_json_tolerant(target) == {"k": 2}


def test_read_json_tolerant_missing_returns_none(tmp_path: Path) -> None:
    assert read_json_tolerant(tmp_path / "missing.json") is None


def test_read_json_tolerant_corrupt_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{not json")
    assert read_json_tolerant(target) is None


def test_read_json_tolerant_non_dict_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]")
    assert read_json_tolerant(target) is None


def test_read_ply_bbox_and_count_missing_returns_none(tmp_path: Path) -> None:
    n, bbox = read_ply_bbox_and_count(tmp_path / "nope.ply")
    assert n is None and bbox is None


def test_read_ply_bbox_and_count_real_ply(tmp_path: Path) -> None:
    """Generate a tiny ply with plyfile and read back the bbox."""
    import numpy as np
    from plyfile import PlyData, PlyElement

    verts = np.array(
        [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (-1.0, -2.0, -3.0)],
        dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")],
    )
    ply_path = tmp_path / "tiny.ply"
    PlyData([PlyElement.describe(verts, "vertex")], text=True).write(ply_path)

    n, bbox = read_ply_bbox_and_count(ply_path)
    assert n == 3
    assert bbox == [[-1.0, -2.0, -3.0], [1.0, 2.0, 3.0]]
