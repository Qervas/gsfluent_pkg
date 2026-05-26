"""Cache-header + Range + 304 conformance for GET /api/sequences/{name}/cache/splats.gsq.

The .gsq cache for a sequence is treated as immutable: once produced for a
given (name, size, mtime), the bytes never change. The server emits a weak
ETag of the form '"<size>-<mtime_int>"' and Cache-Control: public, immutable,
max-age=31536000. Clients send If-None-Match on a refresh
to skip the body entirely (-> 304), or Range on a resume (-> 206).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from gsfluent.api import sequences as seq_api
from gsfluent.core import library as lib

# --------- fixtures ---------------------------------------------------------


@pytest.fixture
def cache_setup(client, tmp_path: Path, monkeypatch) -> dict:
    """Stand up a tmp library with one sequence directory and a synthetic
    .gsq cache file under work/cache/splats/.

    The .gsq body is arbitrary bytes — these tests cover HTTP semantics,
    not codec correctness. The route only cares about (a) sequence exists
    in library, (b) <name>.gsq exists in _SPLAT_CACHE.
    """
    sequences_dir = tmp_path / "library" / "sequences"
    cache_dir = tmp_path / "work" / "cache" / "splats"
    sequences_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    seq_name = "demo"
    (sequences_dir / seq_name).mkdir()
    body = b"A" * 4096 + b"B" * 4096  # 8 KiB of distinguishable bytes
    gsq_path = cache_dir / f"{seq_name}.gsq"
    gsq_path.write_bytes(body)

    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_SPLAT_CACHE", cache_dir)

    return {
        "client": client,
        "name": seq_name,
        "path": gsq_path,
        "body": body,
        "size": len(body),
    }


def _parse_etag(etag: str) -> tuple[int, int]:
    """Extract (size, mtime_int) from the quoted weak ETag '"<size>-<mtime>"'."""
    m = re.fullmatch(r'"(\d+)-(\d+)"', etag)
    assert m is not None, f"unexpected ETag shape: {etag!r}"
    return int(m.group(1)), int(m.group(2))


# --------- happy path: 200 with cache headers -------------------------------


def test_200_response_carries_cache_control_immutable(cache_setup) -> None:
    r = cache_setup["client"].get(f"/api/sequences/{cache_setup['name']}/cache/splats.gsq")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "public" in cc
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_200_response_carries_etag_size_mtime(cache_setup) -> None:
    r = cache_setup["client"].get(f"/api/sequences/{cache_setup['name']}/cache/splats.gsq")
    assert r.status_code == 200
    etag = r.headers.get("etag")
    assert etag is not None
    size, _ = _parse_etag(etag)
    assert size == cache_setup["size"]


def test_200_response_returns_full_body(cache_setup) -> None:
    r = cache_setup["client"].get(f"/api/sequences/{cache_setup['name']}/cache/splats.gsq")
    assert r.status_code == 200
    assert r.content == cache_setup["body"]


# --------- 304 on If-None-Match match ---------------------------------------


def test_if_none_match_returns_304_when_etag_matches(cache_setup) -> None:
    head_r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq"
    )
    etag = head_r.headers["etag"]

    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"If-None-Match": etag},
    )
    assert r.status_code == 304
    # 304 MUST repeat the ETag per RFC 7232; the client uses it to keep its
    # local copy authoritative.
    assert r.headers.get("etag") == etag
    # 304 carries no body.
    assert r.content == b""


def test_if_none_match_mismatch_returns_full_body(cache_setup) -> None:
    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"If-None-Match": '"bogus-etag"'},
    )
    assert r.status_code == 200
    assert r.content == cache_setup["body"]


# --------- 206 on Range request (FastAPI FileResponse provides this) --------


def test_range_request_returns_206_partial_content(cache_setup) -> None:
    """FastAPI's FileResponse already implements byte-range. This test pins
    the contract so a future swap to a custom response doesn't regress it."""
    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"Range": "bytes=4096-8191"},
    )
    assert r.status_code == 206
    assert r.content == cache_setup["body"][4096:8192]
    cr = r.headers.get("content-range", "")
    # Shape: "bytes 4096-8191/8192"
    assert cr.startswith("bytes 4096-8191/")


def test_range_request_open_ended_to_eof(cache_setup) -> None:
    """`Range: bytes=N-` (no end) means resume to EOF — the exact pattern
    the client sends when a .partial exists."""
    n = 4096
    r = cache_setup["client"].get(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"Range": f"bytes={n}-"},
    )
    assert r.status_code == 206
    assert r.content == cache_setup["body"][n:]


# --------- HEAD: metadata without a body (download-size probe) --------------


def test_head_returns_200_with_no_body(cache_setup) -> None:
    """A production client (any frontend, not just our demo) wants the
    download size + range-support up front, without pulling the bytes.
    HEAD must mirror the GET headers but carry an empty body."""
    r = cache_setup["client"].head(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq"
    )
    assert r.status_code == 200
    assert r.content == b""  # header-only


def test_head_carries_content_length_etag_and_ranges(cache_setup) -> None:
    r = cache_setup["client"].head(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq"
    )
    assert r.status_code == 200
    # Content-Length is the progress-bar denominator the client reads first.
    assert r.headers.get("content-length") == str(cache_setup["size"])
    # Accept-Ranges advertises resumable / chunked download.
    assert r.headers.get("accept-ranges") == "bytes"
    # Same immutable-cache contract as GET.
    etag = r.headers.get("etag")
    assert etag is not None
    size, _ = _parse_etag(etag)
    assert size == cache_setup["size"]
    cc = r.headers.get("cache-control", "")
    assert "immutable" in cc


def test_head_304_when_etag_matches(cache_setup) -> None:
    """HEAD honours conditional requests too — a client that already has the
    file confirms freshness with zero bytes transferred."""
    etag = cache_setup["client"].head(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq"
    ).headers["etag"]
    r = cache_setup["client"].head(
        f"/api/sequences/{cache_setup['name']}/cache/splats.gsq",
        headers={"If-None-Match": etag},
    )
    assert r.status_code == 304
    assert r.headers.get("etag") == etag
    assert r.content == b""


# --------- 404 path unchanged ----------------------------------------------


def test_404_when_sequence_dir_missing(cache_setup) -> None:
    r = cache_setup["client"].get("/api/sequences/no-such-seq/cache/splats.gsq")
    assert r.status_code == 404


def test_404_when_gsq_not_built(cache_setup, tmp_path: Path) -> None:
    """Sequence directory exists, but no <name>.gsq under the cache."""
    name = "no-cache-built"
    (lib.SEQUENCES_DIR / name).mkdir()
    r = cache_setup["client"].get(f"/api/sequences/{name}/cache/splats.gsq")
    assert r.status_code == 404
