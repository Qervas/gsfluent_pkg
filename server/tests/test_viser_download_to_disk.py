"""Tests for vh._download_gsq_to_disk — silent full-layer fetch.

Downloads a .gsq via HTTP (served by a local ThreadingHTTPServer) and
asserts byte-identical output without any .partial left behind.

Reuses _write_minimal_gsq from test_splat_ring to avoid duplicating the
fixture builder.
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

import pytest

# ----- import viser_headless (viser/uvicorn are installed in the venv) -------

_VH = Path(__file__).resolve().parents[2] / "frontend" / "python"
sys.path.insert(0, str(_VH))
import viser_headless as vh  # noqa: E402

# ----- reuse the .gsq builder from test_splat_ring ---------------------------
# Import it directly from the sibling test file via importlib so we never
# duplicate the fixture code.
import importlib.util as _ilu

_RING_TEST = Path(__file__).resolve().parent / "test_splat_ring.py"
_spec = _ilu.spec_from_file_location("_tsplat_ring_mod", _RING_TEST)
_tsplat = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_tsplat)  # type: ignore[union-attr]
_write_minimal_gsq = _tsplat._write_minimal_gsq


# ----- tiny HTTP server helper -----------------------------------------------


def _serve_dir(directory: Path):
    """Spin up a ThreadingHTTPServer serving `directory`. Returns (srv, base_url)."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(directory),
    )
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ----- tests -----------------------------------------------------------------


def test_download_gsq_to_disk_byte_identical(tmp_path):
    """_download_gsq_to_disk writes the file byte-for-byte and cleans .partial."""
    # Build a small fixture .gsq.
    src_gsq = tmp_path / "srv" / "a.gsq"
    _write_minimal_gsq(src_gsq, n_splats=4, n_frames=6)
    gsq_bytes = src_gsq.read_bytes()

    srv, base = _serve_dir(tmp_path / "srv")
    try:
        dest = tmp_path / "out.gsq"
        n = vh._download_gsq_to_disk(f"{base}/a.gsq", dest)

        assert dest.read_bytes() == gsq_bytes, "downloaded bytes differ from source"
        assert n == len(gsq_bytes), "returned byte count does not match file size"
        assert not dest.with_suffix(".gsq.partial").exists(), ".partial was not removed"
    finally:
        srv.shutdown()


def test_download_gsq_to_disk_no_partial_left_on_success(tmp_path):
    """No stale .partial file remains after a clean download."""
    src_gsq = tmp_path / "srv" / "b.gsq"
    _write_minimal_gsq(src_gsq, n_splats=4, n_frames=4)

    srv, base = _serve_dir(tmp_path / "srv")
    try:
        dest = tmp_path / "out_b.gsq"
        vh._download_gsq_to_disk(f"{base}/b.gsq", dest)

        partial = dest.with_suffix(".gsq.partial")
        assert not partial.exists(), f"stale .partial found at {partial}"
        assert dest.is_file()
    finally:
        srv.shutdown()


def test_download_gsq_to_disk_bad_url_raises(tmp_path):
    """A 404 response raises RuntimeError."""
    src_dir = tmp_path / "srv"
    src_dir.mkdir()

    srv, base = _serve_dir(src_dir)
    try:
        dest = tmp_path / "missing.gsq"
        with pytest.raises(RuntimeError, match="HTTP 404"):
            vh._download_gsq_to_disk(f"{base}/does_not_exist.gsq", dest)
    finally:
        srv.shutdown()
