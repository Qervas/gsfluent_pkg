"""Phase 2 smoke tests for /api/sequences and library.import_sequence.

Synthesizes minimal full-3DGS .ply fixtures (n=2 splats with all required
attrs: positions, SH DC, scales, rotations, opacity) and exercises:
  - direct import_sequence (happy path + broken-source)
  - POST /api/sequences/import (200 + 409 dup + 422 invalid frame)
  - GET /api/sequences (lists + carries is_broken)
  - GET /api/sequences/{name}/frame/{idx}.ply (alias of /api/runs path)
  - DELETE /api/sequences/{name}
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

# Required full-3DGS attribute set, mirrors library._FULL_3DGS_ATTRS.
_FULL_DTYPE = [
    ("x", "f4"), ("y", "f4"), ("z", "f4"),
    ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
    ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
    ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ("opacity", "f4"),
]
_XYZ_ONLY_DTYPE = [("x", "f4"), ("y", "f4"), ("z", "f4")]


def _write_full_ply(path: Path, n: int = 2) -> None:
    """Write a tiny full-3DGS .ply at `path` with `n` splats."""
    rng = np.random.default_rng(42)
    arr = np.zeros(n, dtype=_FULL_DTYPE)
    arr["x"] = rng.uniform(-1, 1, n)
    arr["y"] = rng.uniform(-1, 1, n)
    arr["z"] = rng.uniform(0, 1, n)
    # SH DC, scales, rotation quat (1,0,0,0), opacity all defaulted.
    arr["rot_0"] = 1.0
    arr["opacity"] = 1.0
    elem = PlyElement.describe(arr, "vertex")
    PlyData([elem]).write(str(path))


def _write_xyz_ply(path: Path, n: int = 2) -> None:
    """Write an xyz-only .ply (NOT a full 3DGS)."""
    arr = np.zeros(n, dtype=_XYZ_ONLY_DTYPE)
    elem = PlyElement.describe(arr, "vertex")
    PlyData([elem]).write(str(path))


def _isolate(monkeypatch, tmp_path):
    """Redirect SEQUENCES_DIR + MODELS_DIR + LIBRARY_ROOT to tmp paths so
    tests don't pollute the real work/library/."""
    from gsfluent.core import library, runner
    monkeypatch.setattr(library, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(library, "SEQUENCES_DIR", tmp_path / "library" / "sequences")
    monkeypatch.setattr(library, "MODELS_DIR", tmp_path / "library" / "models")
    monkeypatch.setattr(runner, "FUSED_DIR", tmp_path / "fused")


# --- direct library.import_sequence ----------------------------------------


def test_import_sequence_happy_path(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import Sequence, import_sequence

    src = tmp_path / "external_sim_001"
    src.mkdir()
    for i in range(3):
        _write_full_ply(src / f"frame_{i:04d}.ply")

    seq = import_sequence(src)

    assert Sequence.exists(seq.name)
    assert seq.name == "external_sim_001"
    assert seq.frame_count() == 3
    assert seq.is_broken is False
    assert seq.meta is not None
    assert seq.meta["kind"] == "sequence"
    assert seq.meta["source"] == "import"
    assert seq.meta["source_path"] == str(src.resolve())
    assert seq.meta["frame_count"] == 3
    assert seq.meta["fps_hint"] == 24
    assert seq.meta["n_splats"] == 2
    assert seq.meta["coord_convention"] == "z-up"
    assert seq.meta["first_frame_full"] is True
    # bbox is computed
    assert seq.meta.get("bbox_initial") is not None


def test_import_sequence_explicit_name(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import Sequence, import_sequence

    src = tmp_path / "raw_dump"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    seq = import_sequence(src, name="my_pretty_name")
    assert seq.name == "my_pretty_name"
    assert Sequence.exists("my_pretty_name")


def test_import_sequence_dup_raises_file_exists(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import import_sequence

    src = tmp_path / "twice"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    import_sequence(src)
    with pytest.raises(FileExistsError):
        import_sequence(src)


def test_import_sequence_rejects_xyz_only(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import import_sequence

    src = tmp_path / "xyz_only"
    src.mkdir()
    _write_xyz_ply(src / "frame_0000.ply")
    with pytest.raises(ImportError, match="not a full 3DGS"):
        import_sequence(src)


def test_import_sequence_rejects_empty(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import import_sequence

    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(ImportError, match="no frame_"):
        import_sequence(src)


def test_import_sequence_rejects_missing_dir(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import import_sequence

    with pytest.raises(FileNotFoundError):
        import_sequence(tmp_path / "does_not_exist")


def test_convert_y_up_materializes_frames(tmp_path, monkeypatch):
    """Phase 4: convert_y_up=True copies + rewrites frames Y-up -> Z-up
    instead of symlinking. The library entry is then self-contained
    (no symlink) and `converted_from` is recorded in meta."""
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core import library
    from gsfluent.core.library import import_sequence

    src = tmp_path / "yup_attempt"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    _write_full_ply(src / "frame_0001.ply")

    seq = import_sequence(src, convert_y_up=True)
    assert seq.frame_count() == 2
    # Materialized: frames/ is a real dir, not a symlink.
    frames_dir = seq.path / "frames"
    assert frames_dir.is_dir()
    assert not frames_dir.is_symlink()
    # Both frame files were rewritten into the library.
    assert (frames_dir / "frame_0000.ply").is_file()
    assert (frames_dir / "frame_0001.ply").is_file()
    assert seq.meta is not None
    assert seq.meta["converted_from"] == "y-up"
    assert seq.meta["coord_convention"] == "z-up"
    # source_path still points at the original (audit trail).
    assert seq.meta["source_path"] == str(src.resolve())
    # is_broken is False even if the source goes away — we materialized.
    import shutil as _sh
    _sh.rmtree(src)
    seq2 = library.Sequence.load(seq.name)
    assert seq2 is not None
    assert seq2.is_broken is False
    assert seq2.frame_count() == 2


def test_is_broken_after_source_removal(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core.library import Sequence, import_sequence

    src = tmp_path / "ephemeral"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    seq = import_sequence(src)
    assert seq.is_broken is False

    shutil.rmtree(src)
    seq2 = Sequence.load(seq.name)
    assert seq2 is not None
    assert seq2.is_broken is True
    # frame_paths() returns [] when the symlink is dangling.
    assert seq2.frame_paths() == []


def test_is_broken_false_for_real_dir(tmp_path, monkeypatch):
    """Sim-produced sequences (real frames/ dir, not a symlink) are
    never marked broken — the property is symlink-specific."""
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core import library
    from gsfluent.core.library import Sequence

    seq_dir = library.SEQUENCES_DIR / "real_dir_seq"
    (seq_dir / "frames").mkdir(parents=True)
    _write_full_ply(seq_dir / "frames" / "frame_0000.ply")
    Sequence.write_meta(
        name="real_dir_seq",
        source="sim",
        frame_count=1,
        n_splats=2,
        first_frame_full=True,
    )
    seq = Sequence.load("real_dir_seq")
    assert seq is not None
    assert seq.is_broken is False


# --- FastAPI endpoint exercises --------------------------------------------


def test_post_import_endpoint_happy(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "ext_alpha"
    src.mkdir()
    for i in range(2):
        _write_full_ply(src / f"frame_{i:04d}.ply")

    r = client.post("/api/sequences/import", json={"folder_path": str(src)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "ext_alpha"
    assert body["source"] == "import"
    assert body["frame_count"] == 2
    assert body["is_broken"] is False


def test_post_import_endpoint_dup_409(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "ext_beta"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")

    r1 = client.post("/api/sequences/import", json={"folder_path": str(src)})
    assert r1.status_code == 200
    r2 = client.post("/api/sequences/import", json={"folder_path": str(src)})
    assert r2.status_code == 409


def test_post_import_endpoint_invalid_frame_422(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "ext_xyz"
    src.mkdir()
    _write_xyz_ply(src / "frame_0000.ply")

    r = client.post("/api/sequences/import", json={"folder_path": str(src)})
    assert r.status_code == 422
    assert "not a full 3DGS" in r.json()["detail"]


def test_post_import_endpoint_missing_dir_422(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    r = client.post(
        "/api/sequences/import", json={"folder_path": str(tmp_path / "ghost")},
    )
    assert r.status_code == 422


def test_post_import_endpoint_convert_y_up_200(client, tmp_path, monkeypatch):
    """Phase 4: convert_y_up=True is now implemented end-to-end. The
    response body carries `converted_from = "y-up"` so the frontend
    can flag it in the Outliner."""
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "yup_ep"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    r = client.post(
        "/api/sequences/import",
        json={"folder_path": str(src), "convert_y_up": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converted_from"] == "y-up"
    assert body["coord_convention"] == "z-up"


def test_get_list_includes_imported(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "ext_gamma"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    client.post("/api/sequences/import", json={"folder_path": str(src)})

    r = client.get("/api/sequences")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()]
    assert "ext_gamma" in names


def test_get_list_includes_sim_sequences(client, tmp_path, monkeypatch):
    """Sim-produced sequences (real dir + source=sim) appear in the
    same list as imports — that's the whole point of unifying them."""
    _isolate(monkeypatch, tmp_path)
    from gsfluent.core import library
    from gsfluent.core.library import Sequence

    seq_dir = library.SEQUENCES_DIR / "sim_seq"
    (seq_dir / "frames").mkdir(parents=True)
    _write_full_ply(seq_dir / "frames" / "frame_0000.ply")
    Sequence.write_meta(
        name="sim_seq",
        source="sim",
        frame_count=1,
        n_splats=2,
    )

    r = client.get("/api/sequences")
    assert r.status_code == 200
    body = r.json()
    sim_entry = next((x for x in body if x["name"] == "sim_seq"), None)
    assert sim_entry is not None
    assert sim_entry["source"] == "sim"
    assert sim_entry["is_broken"] is False


def test_get_frame_alias(client, tmp_path, monkeypatch):
    """The /api/sequences/{name}/frame/{idx}.ply endpoint should serve
    the same bytes as the legacy /api/runs/.../frame path."""
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "ext_frame_test"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    client.post("/api/sequences/import", json={"folder_path": str(src)})

    r = client.get("/api/sequences/ext_frame_test/frame/0.ply")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert r.content[:3] == b"ply"  # ply magic header

    # Same file via the legacy alias.
    r2 = client.get("/api/runs/ext_frame_test/frame/0.ply")
    assert r2.status_code == 200
    assert r2.content == r.content


def test_delete_endpoint(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    src = tmp_path / "ext_del"
    src.mkdir()
    _write_full_ply(src / "frame_0000.ply")
    client.post("/api/sequences/import", json={"folder_path": str(src)})

    r = client.delete("/api/sequences/ext_del")
    assert r.status_code == 200
    assert r.json() == {"deleted": "ext_del"}

    r2 = client.get("/api/sequences")
    names = [x["name"] for x in r2.json()]
    assert "ext_del" not in names

    # The library entry is gone but the source folder is preserved
    # (this is the whole point of symlink semantics for imports).
    assert src.is_dir()
    assert (src / "frame_0000.ply").is_file()


def test_delete_endpoint_404(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    r = client.delete("/api/sequences/never_existed")
    assert r.status_code == 404
