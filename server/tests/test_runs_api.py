def _isolate(monkeypatch, tmp_path):
    """Redirect runner.FUSED_DIR + library.SEQUENCES_DIR to tmp paths so
    tests don't pick up real production data sitting in work/library/."""
    from gsfluent.core import library, runner
    monkeypatch.setattr(library, "LIBRARY_ROOT", tmp_path / "library")
    monkeypatch.setattr(library, "SEQUENCES_DIR", tmp_path / "library" / "sequences")
    monkeypatch.setattr(library, "MODELS_DIR", tmp_path / "library" / "models")
    monkeypatch.setattr(runner, "FUSED_DIR", tmp_path / "fused")


def test_runs_list_starts_empty(client, tmp_path, monkeypatch):
    # The runner registry may have leftovers from earlier tests; ensure clean.
    from gsfluent.core import runner
    _isolate(monkeypatch, tmp_path)
    runner._RUNS.clear()
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_history_reads_fused_dir(client, tmp_path, monkeypatch):
    """A pre-migration fused dir with manifest.json still surfaces in
    history (legacy fallback path)."""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "fused"
    (f / "alpha").mkdir(parents=True)
    (f / "alpha" / "manifest.json").write_text(
        '{"run_name":"alpha","status":"done","started_at":1,"particles":1000}'
    )
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    assert any(x["run_name"] == "alpha" for x in rr.json())


def test_history_handles_missing_fused_dir(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    assert rr.json() == []


def test_history_skips_dirs_without_manifest(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "fused"
    (f / "no_manifest").mkdir(parents=True)
    (f / "with_manifest").mkdir(parents=True)
    (f / "with_manifest" / "manifest.json").write_text(
        '{"run_name":"with_manifest","status":"done","started_at":1}'
    )
    rr = client.get("/api/runs/history")
    listed = [x["run_name"] for x in rr.json()]
    assert "with_manifest" in listed
    assert "no_manifest" not in listed


def test_history_skips_corrupt_manifest(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "fused"
    (f / "corrupt").mkdir(parents=True)
    (f / "corrupt" / "manifest.json").write_text("{not valid json")
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
    # Phase 3 envelope: detail = {"error": {"kind", "message", "details", "trace_id"}}
    body_json = r.json()
    envelope = body_json["detail"] if "detail" in body_json else body_json
    msg = envelope["error"]["message"].lower()
    assert "model_path" in msg or "exist" in msg
    assert envelope["error"]["kind"] == "validation.model_path"


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
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "fused"
    (f / "ghost_run").mkdir(parents=True)
    # Manifest with NO run_name field
    (f / "ghost_run" / "manifest.json").write_text('{"status":"done","started_at":1}')
    rr = client.get("/api/runs/history")
    listed = [x["run_name"] for x in rr.json()]
    assert "ghost_run" in listed


def test_history_keeps_valid_entries_when_one_is_corrupt(client, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "fused"
    (f / "good").mkdir(parents=True)
    (f / "good" / "manifest.json").write_text('{"run_name":"good","status":"done","started_at":1}')
    (f / "bad").mkdir(parents=True)
    (f / "bad" / "manifest.json").write_text("not valid json")
    rr = client.get("/api/runs/history")
    names = [x["run_name"] for x in rr.json()]
    assert "good" in names
    assert "bad" not in names


def test_history_includes_legacy_dirs_without_manifest(client, tmp_path, monkeypatch):
    """Legacy fused dirs (no manifest.json) should still surface in history,
    with a synthesized minimal entry."""
    _isolate(monkeypatch, tmp_path)
    f = tmp_path / "fused"
    legacy = f / "legacy_run"
    legacy.mkdir(parents=True)
    (legacy / "frame_0000.ply").write_text("ply")
    (legacy / "frame_0001.ply").write_text("ply")
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    body = rr.json()
    assert any(x["run_name"] == "legacy_run" and x.get("_synthetic") is True for x in body)


def test_history_reads_library_sequences(client, tmp_path, monkeypatch):
    """Library sequences (post-migration layout) surface with merged
    metadata from both manifest.json and _meta.json."""
    _isolate(monkeypatch, tmp_path)
    seq_dir = tmp_path / "library" / "sequences" / "alpha"
    (seq_dir / "frames").mkdir(parents=True)
    (seq_dir / "frames" / "frame_0000.ply").write_text("ply")
    (seq_dir / "_meta.json").write_text(
        '{"name":"alpha","kind":"sequence","source":"sim",'
        '"model_ref":"my_model","frame_count":1,"fps_hint":24,'
        '"coord_convention":"z-up","first_frame_full":true,'
        '"created_at":"2026-05-09T12:00:00Z"}'
    )
    (seq_dir / "manifest.json").write_text(
        '{"run_name":"alpha","status":"done","started_at":99,'
        '"particles":12345,"recipe_source":"jelly"}'
    )
    rr = client.get("/api/runs/history")
    assert rr.status_code == 200
    body = rr.json()
    entry = next((x for x in body if x["run_name"] == "alpha"), None)
    assert entry is not None
    assert entry["status"] == "done"
    assert entry["started_at"] == 99
    assert entry["particles"] == 12345
    assert entry["recipe_source"] == "jelly"
    assert entry["model_ref"] == "my_model"
    assert entry["sequence_source"] == "sim"
