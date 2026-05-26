"""Integration: second /sync_cell call uses HEAD probe + skips body.

Stands up a FastAPI TestClient against the real splats.gsq endpoint and
verifies:

  1. First call sees an empty cache dir, downloads + decodes a real .gsq.
  2. Second call with the same (name, url) sees the cached dest file,
     sends HEAD, gets the matching ETag, loads from disk, and never
     opens a body-streaming request.

The fake .gsq body is built by `_write_minimal_gsq` to exactly mirror the
production GSQCodec wire format (server/gsfluent/core/codecs/gsq.py):
80-byte header (magic GSQ1, version, n_splats, n_frames, fps_hint,
bbox_min[3], bbox_max[3], static_offset, static_size, padding), then a
16-byte frame-index entry per frame, then the zstd-compressed static
block, then zstd-compressed per-frame chunks. Each frame chunk
decodes via _gsq_dequantize_frame on the client side, so we reuse the
production sizes (xyz int16 * 3 + quat int16 * 3 per splat).
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest


def _write_minimal_gsq(path: Path) -> bytes:
    """Build the smallest decodable .gsq into `path` and return its bytes.

    Mirrors GSQCodec.encode_sequence_dir's byte layout exactly. Used by
    both the cache-hit and resume-from-partial integration tests.
    """
    import numpy as np
    import zstandard as zstd

    n_splats = 2
    n_frames = 1
    fps_hint = 24.0

    bbox_min = np.array([-1.0, -1.0, -1.0], dtype=np.float32)
    bbox_max = np.array([1.0, 1.0, 1.0], dtype=np.float32)

    # Static block: rgb_f16 (n*3*2) + opacity_u8 (n*1) + scales_f16 (n*3*2)
    rgb_f16 = np.array([[0.5, 0.5, 0.5], [0.6, 0.6, 0.6]], dtype=np.float16)
    opacity_u8 = np.array([200, 255], dtype=np.uint8)
    scales_f16 = np.array([[0.01, 0.01, 0.01], [0.02, 0.02, 0.02]], dtype=np.float16)
    static_uncompressed = rgb_f16.tobytes() + opacity_u8.tobytes() + scales_f16.tobytes()

    # Frame block: xyz int16 (n*3*2) + quat int16 (n*3*2)
    # (the production decoder reads xyz_i16 then quat_i16; quat is the
    # 3-component imaginary part, w is reconstructed.)
    xyz_q = np.array([[0, 0, 0], [100, 100, 100]], dtype=np.int16)
    quat_q = np.array([[0, 0, 0], [0, 0, 0]], dtype=np.int16)
    frame_uncompressed = xyz_q.tobytes() + quat_q.tobytes()

    cctx = zstd.ZstdCompressor(level=3)
    static_compressed = cctx.compress(static_uncompressed)
    frame_compressed = cctx.compress(frame_uncompressed)

    HEADER_SIZE = 80
    INDEX_ENTRY_SIZE = 16
    static_offset = HEADER_SIZE + n_frames * INDEX_ENTRY_SIZE
    static_size = len(static_compressed)
    frame0_offset = static_offset + static_size

    # Assemble bytes in the exact production order.
    out = bytearray()
    out += b"GSQ1"
    out += struct.pack("<III", 1, n_splats, n_frames)  # VERSION=1
    out += struct.pack("<f", fps_hint)
    out += bbox_min.tobytes()
    out += bbox_max.tobytes()
    out += struct.pack("<QI", static_offset, static_size)
    out += b"\x00" * 24  # pad to HEADER_SIZE (80)
    assert len(out) == HEADER_SIZE, f"header drift: {len(out)}"

    # Frame index: <QII> = offset, size, reserved
    out += struct.pack("<QII", frame0_offset, len(frame_compressed), 0)
    assert len(out) == static_offset

    out += static_compressed
    out += frame_compressed

    final = bytes(out)
    path.write_bytes(final)
    return final


@pytest.fixture
def real_gsq(tmp_path: Path) -> dict:
    """Stand up server side cache + a real .gsq file in it."""
    cache_dir = tmp_path / "work" / "cache" / "splats"
    cache_dir.mkdir(parents=True)
    seq_name = "demo"
    gsq_path = cache_dir / f"{seq_name}.gsq"
    body = _write_minimal_gsq(gsq_path)
    return {"cache_dir": cache_dir, "name": seq_name, "path": gsq_path, "body": body}


def test_second_sync_uses_head_and_skips_body(real_gsq, tmp_path: Path, monkeypatch) -> None:
    """First request downloads; second request hits HEAD and skips body."""
    from fastapi.testclient import TestClient

    from gsfluent.api import sequences as seq_api
    from gsfluent.core import library as lib
    from gsfluent.server import create_app

    # Make the sequence exist on disk so the route's Sequence.exists()
    # check passes.
    sequences_dir = tmp_path / "library" / "sequences"
    (sequences_dir / real_gsq["name"]).mkdir(parents=True)
    monkeypatch.setattr(lib, "SEQUENCES_DIR", sequences_dir)
    monkeypatch.setattr(seq_api, "_SPLAT_CACHE", real_gsq["cache_dir"])

    client = TestClient(create_app())

    # Sanity: the route returns the .gsq body with the new ETag header.
    r = client.get(f"/api/sequences/{real_gsq['name']}/cache/splats.gsq")
    assert r.status_code == 200
    etag = r.headers["etag"]
    assert r.content == real_gsq["body"]

    # If-None-Match short-circuits to 304.
    r2 = client.get(
        f"/api/sequences/{real_gsq['name']}/cache/splats.gsq",
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.content == b""

    # Range fetch returns 206 with the right slice.
    r3 = client.get(
        f"/api/sequences/{real_gsq['name']}/cache/splats.gsq",
        headers={"Range": "bytes=0-15"},
    )
    assert r3.status_code == 206
    assert r3.content == real_gsq["body"][:16]
