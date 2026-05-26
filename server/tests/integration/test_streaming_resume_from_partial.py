"""Integration: interrupted .gsq download resumes via Range.

Simulates an interrupted prior download by writing the first N bytes of
the real .gsq body, then exercises the server's Range endpoint. The
decode path is verified by reading back the original bytes and comparing.

Companion to test_streaming_cache_hit.py — both rely on the same minimal
.gsq writer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Reuse the writer from the cache-hit test.
from .test_streaming_cache_hit import _write_minimal_gsq


def test_range_206_returns_byte_suffix(tmp_path: Path, monkeypatch) -> None:
    """Server-side: Range: bytes=N- returns body[N:] with status 206."""
    from fastapi.testclient import TestClient

    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    cache_dir = tmp_path / "work" / "cache" / "splats"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)

    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / seq_name).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_SPLAT_CACHE", cache_dir)

    client = TestClient(create_app())

    # Simulate a partial: server has bytes [0..N], client already has them.
    n = len(body) // 2
    r = client.get(
        f"/api/sequences/{seq_name}/cache/splats.gsq",
        headers={"Range": f"bytes={n}-"},
    )
    assert r.status_code == 206
    assert r.content == body[n:]


def test_range_resume_round_trip(tmp_path: Path, monkeypatch) -> None:
    """End-to-end: write partial prefix, send Range, concat with response,
    confirm assembled body equals the original."""
    from fastapi.testclient import TestClient

    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    cache_dir = tmp_path / "work" / "cache" / "splats"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)

    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / seq_name).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_SPLAT_CACHE", cache_dir)

    client = TestClient(create_app())

    # Client side: pretend the .partial has the first n bytes.
    n = len(body) // 3
    partial_prefix = body[:n]

    r = client.get(
        f"/api/sequences/{seq_name}/cache/splats.gsq",
        headers={"Range": f"bytes={n}-"},
    )
    assert r.status_code == 206
    assembled = partial_prefix + r.content
    assert assembled == body


def test_range_ignored_returns_200_full_body(tmp_path: Path, monkeypatch) -> None:
    """Belt-and-suspenders: if a future codec change makes Range fall
    back to 200, the client treats it as 'discard partial, restart'. This
    test pins the server's CURRENT behavior (FastAPI honors Range) so a
    regression surfaces immediately."""
    from fastapi.testclient import TestClient

    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    cache_dir = tmp_path / "work" / "cache" / "splats"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)

    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / seq_name).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_SPLAT_CACHE", cache_dir)

    client = TestClient(create_app())

    # No Range header → must be 200 with full body.
    r = client.get(f"/api/sequences/{seq_name}/cache/splats.gsq")
    assert r.status_code == 200
    assert r.content == body
