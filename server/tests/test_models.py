import io


def test_list_models_empty(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    assert client.get("/api/models").json() == []


def test_upload_ply(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    fake = b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n"
    r = client.post(
        "/api/models/upload",
        files={"file": ("building.ply", io.BytesIO(fake), "application/octet-stream")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("building_")
    assert (tmp_path / "uploads" / body["name"] / "point_cloud" / "iteration_30000" / "point_cloud.ply").exists()
    # History should now contain the model
    listed = client.get("/api/models").json()
    assert any(x["name"] == body["name"] for x in listed)


def test_upload_rejects_non_ply(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    r = client.post(
        "/api/models/upload",
        files={"file": ("not_a_ply.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert r.status_code == 422


def test_upload_rejects_tiny_file(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    r = client.post(
        "/api/models/upload",
        files={"file": ("empty.ply", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 422
