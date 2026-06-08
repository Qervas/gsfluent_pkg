import json

from gsfluent.api import stream


def test_manifest_terminal_status_accepts_failed(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": "failed"}))

    assert stream._manifest_terminal_status(manifest) == "failed"


def test_manifest_terminal_status_normalizes_error_to_failed(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": "error"}))

    assert stream._manifest_terminal_status(manifest) == "failed"


def test_manifest_terminal_status_ignores_running(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"status": "running"}))

    assert stream._manifest_terminal_status(manifest) is None


def test_recent_log_lines_caps_replayed_bytes(tmp_path) -> None:
    log = tmp_path / "run.log"
    log.write_text("old\nmiddle\nnew\n")

    lines, offset = stream._recent_log_lines(log, max_bytes=8)

    assert lines == ["dle", "new"]
    assert offset == log.stat().st_size
