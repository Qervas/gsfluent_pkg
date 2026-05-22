"""Conformance tests for the CacheCodec Protocol.

Phase 2 will implement GSQCodec against this contract. Phase 1 verifies
the Protocol shape with an in-memory stub.
"""
import io
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import BinaryIO

import pytest

from gsfluent.protocols.cache import (
    CacheCodec,
    CacheMetadata,
    CodecError,
    DecodedFrame,
    SplatFrame,
)
from gsfluent.protocols.observability import EventEmitter


class _StubEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context) -> "_StubEmitter": return self


class _IdentityCodec:
    """Stub codec: emits a single 'frame_count' byte then dummy frame bytes."""

    media_type = "application/x-stub"
    file_extension = ".stub"

    def encode(
        self,
        frames: Iterable[SplatFrame],
        out: BinaryIO,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        count = 0
        for _ in frames:
            count += 1
            out.write(b"f")
        return CacheMetadata(n_splats=0, n_frames=count, bbox=(0, 0, 0, 0, 0, 0))

    async def decode_streaming(
        self, src: AsyncIterator[bytes]
    ) -> AsyncIterator[DecodedFrame]:
        async for chunk in src:
            for _ in chunk:
                yield DecodedFrame(frame_index=0, data={})

    def decode_all(self, src: BinaryIO) -> Sequence[DecodedFrame]:
        body = src.read()
        return [DecodedFrame(frame_index=i, data={}) for i in range(len(body))]


def test_stub_satisfies_cache_codec_protocol() -> None:
    codec: CacheCodec = _IdentityCodec()
    assert isinstance(codec, CacheCodec)


def test_codec_has_media_type_and_extension() -> None:
    codec = _IdentityCodec()
    assert codec.media_type == "application/x-stub"
    assert codec.file_extension == ".stub"


def test_encode_returns_metadata() -> None:
    codec = _IdentityCodec()
    out = io.BytesIO()
    meta = codec.encode([{}, {}, {}], out, _StubEmitter())
    assert meta.n_frames == 3
    assert out.getvalue() == b"fff"


def test_codec_error_is_an_exception() -> None:
    with pytest.raises(CodecError):
        raise CodecError("synthetic")


# --- Conformance over real GSQCodec ------------------------------------------

import struct
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def _write_minimal_3dgs_frame(path: Path, n: int = 4, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["opacity"] = 1.0
    verts["scale_0"] = -1.0
    verts["scale_1"] = -1.0
    verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_real_gsq_codec_satisfies_protocol() -> None:
    from gsfluent.core.codecs.gsq import GSQCodec
    c: CacheCodec = GSQCodec()
    assert isinstance(c, CacheCodec)


def test_real_gsq_codec_encode_then_decode_round_trip(tmp_path) -> None:
    """Encode a tiny synthetic sequence, then decode it back."""
    from gsfluent.core.codecs.gsq import GSQCodec

    frames_dir = tmp_path / "seq" / "frames"
    frames_dir.mkdir(parents=True)
    for i in range(3):
        _write_minimal_3dgs_frame(frames_dir / f"frame_{i:04d}.ply", n=4, seed=i)

    out_path = tmp_path / "seq.gsq"
    codec = GSQCodec()
    meta = codec.encode_sequence_dir(frames_dir, out_path, on_event=_StubEmitter())
    assert meta.n_frames == 3
    assert meta.n_splats == 4

    with open(out_path, "rb") as fh:
        decoded = codec.decode_all(fh)
    assert len(decoded) == 3
    assert decoded[0].frame_index == 0
    assert decoded[2].frame_index == 2
