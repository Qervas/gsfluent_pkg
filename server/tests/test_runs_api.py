def test_runs_list_starts_empty(client):
    # The runner registry may have leftovers from earlier tests; ensure clean.
    from gsfluent.core import runner
    runner._RUNS.clear()
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_history_reads_fused_dir(client, tmp_path, monkeypatch):
    from gsfluent.core import runner
    f = tmp_path / "fused"
    (f / "alpha").mkdir(parents=True)
    (f / "alpha" / "manifest.json").write_text(
        '{"run_name":"alpha","status":"done","started_at":1,"particles":1000}'
    )
    monkeypatch.setattr(runner, "FUSED_DIR", f)
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    assert any(x["run_name"] == "alpha" for x in rr.json())


def test_history_handles_missing_fused_dir(client, tmp_path, monkeypatch):
    from gsfluent.core import runner
    monkeypatch.setattr(runner, "FUSED_DIR", tmp_path / "no_such_dir")
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    assert rr.json() == []


def test_history_skips_dirs_without_manifest(client, tmp_path, monkeypatch):
    from gsfluent.core import runner
    f = tmp_path / "fused"
    (f / "no_manifest").mkdir(parents=True)
    (f / "with_manifest").mkdir(parents=True)
    (f / "with_manifest" / "manifest.json").write_text(
        '{"run_name":"with_manifest","status":"done","started_at":1}'
    )
    monkeypatch.setattr(runner, "FUSED_DIR", f)
    rr = client.get("/api/runs/history")
    listed = [x["run_name"] for x in rr.json()]
    assert "with_manifest" in listed
    assert "no_manifest" not in listed


def test_history_skips_corrupt_manifest(client, tmp_path, monkeypatch):
    from gsfluent.core import runner
    f = tmp_path / "fused"
    (f / "corrupt").mkdir(parents=True)
    (f / "corrupt" / "manifest.json").write_text("{not valid json")
    monkeypatch.setattr(runner, "FUSED_DIR", f)
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    assert rr.json() == []   # corrupt entry skipped, no crash


def test_post_validates_payload(client):
    # Missing required fields should 422
    r = client.post("/api/runs", json={})
    assert r.status_code == 422


def test_cancel_unknown_run_404(client):
    from gsfluent.core import runner
    runner._RUNS.clear()
    r = client.delete("/api/runs/nonexistent")
    assert r.status_code == 404


def test_post_returns_422_when_model_path_missing(client, tmp_path):
    body = {
        "run_name": "test_missing",
        "model_path": str(tmp_path / "nope"),
        "recipe_data": {"material": "jelly"},
        "recipe_source": "jelly",
        "particles": 10000,
    }
    r = client.post("/api/runs", json=body)
    assert r.status_code == 422
    assert "model_path" in r.json()["detail"].lower() or "exist" in r.json()["detail"].lower()


def test_post_returns_422_when_model_path_is_file(client, tmp_path):
    bogus = tmp_path / "looks_like_model.txt"
    bogus.write_text("not a directory")
    body = {
        "run_name": "test_file",
        "model_path": str(bogus),
        "recipe_data": {"material": "jelly"},
        "recipe_source": "jelly",
        "particles": 10000,
    }
    r = client.post("/api/runs", json=body)
    assert r.status_code == 422


def test_history_falls_back_to_dir_name_when_run_name_missing(client, tmp_path, monkeypatch):
    from gsfluent.core import runner
    f = tmp_path / "fused"
    (f / "ghost_run").mkdir(parents=True)
    # Manifest with NO run_name field
    (f / "ghost_run" / "manifest.json").write_text('{"status":"done","started_at":1}')
    monkeypatch.setattr(runner, "FUSED_DIR", f)
    rr = client.get("/api/runs/history")
    listed = [x["run_name"] for x in rr.json()]
    assert "ghost_run" in listed


def test_history_keeps_valid_entries_when_one_is_corrupt(client, tmp_path, monkeypatch):
    from gsfluent.core import runner
    f = tmp_path / "fused"
    (f / "good").mkdir(parents=True)
    (f / "good" / "manifest.json").write_text('{"run_name":"good","status":"done","started_at":1}')
    (f / "bad").mkdir(parents=True)
    (f / "bad" / "manifest.json").write_text("not valid json")
    monkeypatch.setattr(runner, "FUSED_DIR", f)
    rr = client.get("/api/runs/history")
    names = [x["run_name"] for x in rr.json()]
    assert "good" in names
    assert "bad" not in names
