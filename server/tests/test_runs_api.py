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
