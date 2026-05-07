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
    # no disk side-effects
    assert not (tmp_path / "uploads").exists() or not any((tmp_path / "uploads").iterdir())
    assert client.get("/api/models").json() == []


def test_upload_rejects_tiny_file(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    r = client.post(
        "/api/models/upload",
        files={"file": ("empty.ply", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert not (tmp_path / "uploads").exists() or not any((tmp_path / "uploads").iterdir())
    assert client.get("/api/models").json() == []


def test_upload_rejects_bad_magic(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    # 100 bytes that don't start with "ply\n" or "ply\r"
    bad = b"x" * 100
    r = client.post(
        "/api/models/upload",
        files={"file": ("fake.ply", io.BytesIO(bad), "application/octet-stream")},
    )
    assert r.status_code == 422
    assert "magic" in r.json()["detail"].lower() or "ply" in r.json()["detail"].lower()
    assert not (tmp_path / "uploads").exists() or not any((tmp_path / "uploads").iterdir())


def test_history_dedupes_by_name(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    # Pre-populate history with a known name (forge an entry, simulating an earlier upload)
    m.record_model("fake_model_1", tmp_path / "uploads" / "fake_model_1")
    m.record_model("fake_model_2", tmp_path / "uploads" / "fake_model_2")
    m.record_model("fake_model_1", tmp_path / "uploads" / "fake_model_1_v2")  # re-record same name
    listed = client.get("/api/models").json()
    names = [x["name"] for x in listed]
    assert names == ["fake_model_1", "fake_model_2"]   # newest first, no dupes
    # And the path should be the latest one
    assert listed[0]["path"].endswith("fake_model_1_v2")


def test_history_caps_at_max(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    monkeypatch.setattr(m, "MAX_HISTORY", 5)  # smaller cap for the test
    for i in range(8):
        m.record_model(f"m_{i}", tmp_path / "uploads" / f"m_{i}")
    listed = client.get("/api/models").json()
    assert len(listed) == 5
    # Newest 5: m_7, m_6, m_5, m_4, m_3
    assert [x["name"] for x in listed] == ["m_7", "m_6", "m_5", "m_4", "m_3"]


def test_register_local_model(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    # Build a fake 3DGS model layout
    model_dir = tmp_path / "my_model"
    iter_dir = model_dir / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True)
    fake_ply = b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n" + b"\x00" * 100
    (iter_dir / "point_cloud.ply").write_bytes(fake_ply)

    r = client.post("/api/models/register", json={"path": str(model_dir)})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "my_model"
    assert body["path"] == str(model_dir)
    # Must appear in history
    listed = client.get("/api/models").json()
    assert any(x["name"] == "my_model" for x in listed)


def test_register_rejects_invalid_path(client, tmp_path, monkeypatch):
    from gsfluent.core import models as m
    monkeypatch.setattr(m, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(m, "HISTORY_FILE", tmp_path / "model_history.json")
    # Path doesn't exist
    r = client.post("/api/models/register", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 422
    # Path exists but no point_cloud/ inside
    bad = tmp_path / "bad_model"
    bad.mkdir()
    r2 = client.post("/api/models/register", json={"path": str(bad)})
    assert r2.status_code == 422
    # Path exists with point_cloud/ but no iteration_*/ inside
    half = tmp_path / "half_model"
    (half / "point_cloud").mkdir(parents=True)
    r3 = client.post("/api/models/register", json={"path": str(half)})
    assert r3.status_code == 422
