"""LOD base-layer route: GET /api/sequences/{name}/cache/base.gsq.

Mirrors test_sequences_cache_headers.py in setup approach. The base.gsq is
the LOD base layer produced by the packer (Task 3); same weak-ETag + Range
+ 304 machinery as splats.gsq. When absent, the route returns 404 so the
client can fall back to full-only streaming.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from gsfluent.api import sequences as seq_api
from gsfluent.core import library as lib


# --------- fixtures ---------------------------------------------------------


@pytest.fixture
def base_setup(client, tmp_path: Path, monkeypatch) -> dict:
    """Stand up a tmp library with one sequence directory plus both
    <name>.gsq and <name>.base.gsq in the viser cache dir.

    The bodies are arbitrary bytes — tests cover HTTP semantics only.
    The route only cares about (a) sequence exists in library, and
    (b) <name>.base.gsq exists in _VISER_CACHE.
    """
    sequences_dir = tmp_path / "library" / "sequences"
    cache_dir = tmp_path / "work" / "cache" / "viser"
    sequences_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    seq_name = "demo"
    (sequences_dir / seq_name).mkdir()

    full_body = b"F" * 4096  # full splats.gsq (must exist for sequence validity)
    (cache_dir / f"{seq_name}.gsq").write_bytes(full_body)

    base_body = b"B" * 2048 + b"C" * 2048  # 4 KiB base layer
    base_path = cache_dir / f"{seq_name}.base.gsq"
    base_path.write_bytes(base_body)

    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", cache_dir)

    return {
        "client": client,
        "name": seq_name,
        "path": base_path,
        "body": base_body,
        "size": len(base_body),
        "sequences_dir": sequences_dir,
        "cache_dir": cache_dir,
    }


@pytest.fixture
def no_base_setup(client, tmp_path: Path, monkeypatch) -> dict:
    """Same as base_setup but WITHOUT the .base.gsq file — tests the 404 path."""
    sequences_dir = tmp_path / "library" / "sequences"
    cache_dir = tmp_path / "work" / "cache" / "viser"
    sequences_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    seq_name = "demo_nobase"
    (sequences_dir / seq_name).mkdir()

    full_body = b"F" * 4096
    (cache_dir / f"{seq_name}.gsq").write_bytes(full_body)
    # Intentionally no <seq_name>.base.gsq written.

    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_VISER_CACHE", cache_dir)

    return {
        "client": client,
        "name": seq_name,
    }


def _parse_etag(etag: str) -> tuple[int, int]:
    """Extract (size, mtime_int) from the quoted weak ETag '"<size>-<mtime>"'."""
    m = re.fullmatch(r'"(\d+)-(\d+)"', etag)
    assert m is not None, f"unexpected ETag shape: {etag!r}"
    return int(m.group(1)), int(m.group(2))


# --------- happy path: 200 with cache headers when base exists ---------------


def test_base_gsq_200_carries_etag(base_setup) -> None:
    r = base_setup["client"].get(
        f"/api/sequences/{base_setup['name']}/cache/base.gsq"
    )
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None
    size, _ = _parse_etag(etag)
    assert size == base_setup["size"]


def test_base_gsq_200_carries_cache_control_public(base_setup) -> None:
    r = base_setup["client"].get(
        f"/api/sequences/{base_setup['name']}/cache/base.gsq"
    )
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert cc.startswith("public")


def test_base_gsq_200_full_body(base_setup) -> None:
    r = base_setup["client"].get(
        f"/api/sequences/{base_setup['name']}/cache/base.gsq"
    )
    assert r.status_code == 200
    assert r.content == base_setup["body"]


# --------- Range: 206 partial content ----------------------------------------


def test_base_gsq_range_returns_206(base_setup) -> None:
    """Range: bytes=0-9 should return 206 with exactly 10 bytes."""
    r = base_setup["client"].get(
        f"/api/sequences/{base_setup['name']}/cache/base.gsq",
        headers={"Range": "bytes=0-9"},
    )
    assert r.status_code == 206
    assert len(r.content) == 10
    assert r.content == base_setup["body"][:10]


# --------- 304 on If-None-Match match ----------------------------------------


def test_base_gsq_304_on_etag_match(base_setup) -> None:
    head_r = base_setup["client"].get(
        f"/api/sequences/{base_setup['name']}/cache/base.gsq"
    )
    etag = head_r.headers["etag"]

    r = base_setup["client"].get(
        f"/api/sequences/{base_setup['name']}/cache/base.gsq",
        headers={"If-None-Match": etag},
    )
    assert r.status_code == 304
    assert r.headers.get("etag") == etag
    assert r.content == b""


# --------- 404 when base layer not built -------------------------------------


def test_base_gsq_404_when_file_absent(no_base_setup) -> None:
    """Sequence exists, full .gsq exists, but no .base.gsq → 404."""
    r = no_base_setup["client"].get(
        f"/api/sequences/{no_base_setup['name']}/cache/base.gsq"
    )
    assert r.status_code == 404


def test_base_gsq_404_when_sequence_missing(base_setup) -> None:
    r = base_setup["client"].get(
        "/api/sequences/no-such-sequence/cache/base.gsq"
    )
    assert r.status_code == 404


# --------- listing advertises base_gsq_bytes when base exists ----------------


def test_listing_has_base_gsq_bytes_when_base_exists(base_setup) -> None:
    """GET /api/sequences must include cache.base_gsq_bytes > 0 for the sequence
    that has a .base.gsq file packed."""
    r = base_setup["client"].get("/api/sequences")
    assert r.status_code == 200
    entries = r.json()
    entry = next((e for e in entries if e["name"] == base_setup["name"]), None)
    assert entry is not None, f"sequence {base_setup['name']!r} not in listing"
    cache = entry.get("cache", {})
    base_bytes = cache.get("base_gsq_bytes")
    assert base_bytes is not None and base_bytes > 0, (
        f"expected cache.base_gsq_bytes > 0, got {base_bytes!r}"
    )


def test_listing_base_gsq_bytes_none_when_no_base(no_base_setup) -> None:
    """When no .base.gsq was packed, base_gsq_bytes should be None (file absent)."""
    r = no_base_setup["client"].get("/api/sequences")
    assert r.status_code == 200
    entries = r.json()
    entry = next((e for e in entries if e["name"] == no_base_setup["name"]), None)
    assert entry is not None, f"sequence {no_base_setup['name']!r} not in listing"
    cache = entry.get("cache", {})
    base_bytes = cache.get("base_gsq_bytes")
    assert base_bytes is None, (
        f"expected cache.base_gsq_bytes to be None (no base built), got {base_bytes!r}"
    )
