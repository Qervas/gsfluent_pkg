"""Tests for the prune step wired into the pack pipeline (tools/pack_splats.py).

Covers GSFLUENT_PRUNE_RETENTION resolution (default / disable / bad value) and
the in-place prune-overwrite helper, so the default-on behavior for NEW
sequences can't silently regress.
"""
import importlib.util
import struct
from pathlib import Path

import numpy as np
import pytest
import zstandard as zstd

# pack_splats.py lives in server/tools/ (outside the package). Load it as a
# module by path so we can unit-test its helpers without spawning a subprocess.
_TOOLS = Path(__file__).resolve().parents[2] / "tools" / "pack_splats.py"
_spec = importlib.util.spec_from_file_location("pack_splats_under_test", _TOOLS)
pack_splats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pack_splats)

from gsfluent.core.codecs.gsq import parse_header_bytes  # noqa: E402


def _make_tiny_gsq(n_splats: int, n_frames: int) -> bytes:
    MAGIC = b"GSQ1"; VERSION = 1
    HEADER_SIZE = 80; INDEX_ENTRY = 16
    cctx = zstd.ZstdCompressor(level=1)
    rng = np.random.default_rng(0)
    rgb = rng.random((n_splats, 3)).astype(np.float16)
    opacity = (rng.random(n_splats) * 255).astype(np.uint8)
    scales = rng.random((n_splats, 3)).astype(np.float16)
    static = rgb.tobytes() + opacity.tobytes() + scales.tobytes()
    static_c = cctx.compress(static)
    static_off = HEADER_SIZE + n_frames * INDEX_ENTRY
    frames_c = []
    for _ in range(n_frames):
        xyz = rng.integers(-100, 100, (n_splats, 3), dtype=np.int16)
        qxyz = rng.integers(-100, 100, (n_splats, 3), dtype=np.int16)
        frames_c.append(cctx.compress(xyz.tobytes() + qxyz.tobytes()))
    out = bytearray()
    out += MAGIC
    out += struct.pack("<III", VERSION, n_splats, n_frames)
    out += struct.pack("<f", 24.0)
    out += np.array([-1, -1, -1], dtype=np.float32).tobytes()
    out += np.array([1, 1, 1], dtype=np.float32).tobytes()
    out += struct.pack("<QI", static_off, len(static_c))
    out += b"\x00" * 24
    off = static_off + len(static_c)
    for c in frames_c:
        out += struct.pack("<QII", off, len(c), 0); off += len(c)
    out += static_c
    for c in frames_c:
        out += c
    return bytes(out)


def test_default_retention_is_0_98(monkeypatch) -> None:
    monkeypatch.delenv("GSFLUENT_PRUNE_RETENTION", raising=False)
    assert pack_splats._resolve_prune_retention() == 0.98
    assert pack_splats.DEFAULT_PRUNE_RETENTION == 0.98


@pytest.mark.parametrize("val", ["0", "", "  "])
def test_disable_via_env(monkeypatch, val) -> None:
    monkeypatch.setenv("GSFLUENT_PRUNE_RETENTION", val)
    assert pack_splats._resolve_prune_retention() == 0.0


def test_custom_retention_via_env(monkeypatch) -> None:
    monkeypatch.setenv("GSFLUENT_PRUNE_RETENTION", "0.99")
    assert pack_splats._resolve_prune_retention() == 0.99


@pytest.mark.parametrize("val", ["abc", "1.5", "-0.2", "2"])
def test_bad_env_disables(monkeypatch, val) -> None:
    monkeypatch.setenv("GSFLUENT_PRUNE_RETENTION", val)
    assert pack_splats._resolve_prune_retention() == 0.0


def test_prune_in_place_overwrites_and_shrinks(tmp_path) -> None:
    out = tmp_path / "seq.gsq"
    raw = _make_tiny_gsq(n_splats=500, n_frames=4)
    out.write_bytes(raw)
    n_before = parse_header_bytes(raw)["n_splats"]

    pack_splats._prune_in_place(out, 0.98)

    pruned = out.read_bytes()
    h = parse_header_bytes(pruned)
    assert h["n_splats"] < n_before
    assert h["n_frames"] == 4
    assert pruned[:4] == b"GSQ1"
    assert len(pruned) < len(raw)


