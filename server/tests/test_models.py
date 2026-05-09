"""Tests for the library-backed model API.

Each test redirects `library.MODELS_DIR` and `library._REGISTERED_INDEX`
to `tmp_path` so the in-process side effects don't pollute the real
work/library/. The legacy `models.UPLOADS_DIR` is kept as an alias and
also patched here for any pre-Phase-1 callers that still reference it.
"""
import io


def _patch_library_paths(monkeypatch, tmp_path):
    from gsfluent.core import library
    from gsfluent.core import models as m
    models_dir = tmp_path / "library" / "models"
    monkeypatch.setattr(library, "MODELS_DIR", models_dir)
    monkeypatch.setattr(library, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(library, "SEQUENCES_DIR", tmp_path / "library" / "sequences")
    monkeypatch.setattr(
        library, "_REGISTERED_INDEX", models_dir / "_registered.json",
    )
    # Legacy alias used by older code paths still pointing at the same dir.
    monkeypatch.setattr(m, "UPLOADS_DIR", models_dir)
    monkeypatch.setattr(m, "MODELS_DIR", models_dir)


def test_list_models_empty(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    assert client.get("/api/models").json() == []


def test_upload_ply(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    fake = b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n"
    r = client.post(
        "/api/models/upload",
        files={"ply": ("building.ply", io.BytesIO(fake), "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("building_")
    models_dir = tmp_path / "library" / "models"
    assert (models_dir / body["name"] / "point_cloud" / "iteration_30000" / "point_cloud.ply").exists()
    assert (models_dir / body["name"] / "_meta.json").exists()
    listed = client.get("/api/models").json()
    assert any(x["name"] == body["name"] for x in listed)


def test_upload_rejects_non_ply(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    r = client.post(
        "/api/models/upload",
        files={"ply": ("not_a_ply.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 422
    models_dir = tmp_path / "library" / "models"
    assert not models_dir.exists() or not any(models_dir.iterdir())
    assert client.get("/api/models").json() == []


def test_upload_rejects_tiny_file(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    r = client.post(
        "/api/models/upload",
        files={"ply": ("empty.ply", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 422
    models_dir = tmp_path / "library" / "models"
    assert not models_dir.exists() or not any(models_dir.iterdir())


def test_upload_rejects_bad_magic(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    bad = b"x" * 100
    r = client.post(
        "/api/models/upload",
        files={"ply": ("fake.ply", io.BytesIO(bad), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert "magic" in r.json()["detail"].lower() or "ply" in r.json()["detail"].lower()


def test_register_local_model(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    # Build a fake 3DGS model layout outside the library root.
    model_dir = tmp_path / "external" / "my_model"
    iter_dir = model_dir / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True)
    fake_ply = b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n" + b"\x00" * 100
    (iter_dir / "point_cloud.ply").write_bytes(fake_ply)

    r = client.post("/api/models/register", json={"path": str(model_dir)})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "my_model"
    assert body["path"] == str(model_dir)
    listed = client.get("/api/models").json()
    assert any(x["name"] == "my_model" for x in listed)


def test_register_rejects_invalid_path(client, tmp_path, monkeypatch):
    _patch_library_paths(monkeypatch, tmp_path)
    r = client.post("/api/models/register", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 422
    bad = tmp_path / "bad_model"
    bad.mkdir()
    r2 = client.post("/api/models/register", json={"path": str(bad)})
    assert r2.status_code == 422
    half = tmp_path / "half_model"
    (half / "point_cloud").mkdir(parents=True)
    r3 = client.post("/api/models/register", json={"path": str(half)})
    assert r3.status_code == 422
