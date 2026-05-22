"""Conformance tests for the CacheCodec Protocol.

Phase 2 will implement GSQCodec against this contract. Phase 1 verifies
the Protocol shape with an in-memory stub.
"""
import io
from typing import AsyncIterator, BinaryIO, Iterable, Sequence

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
