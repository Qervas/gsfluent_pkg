"""CacheCodec Protocol — layer 4.

Encodes/decodes a sequence of splat frames to the codec's wire format.
Concrete: GSQCodec (Phase 2). Swap candidates: SPZ-per-frame, raw-PLY-zstd.

SplatFrame and DecodedFrame use dict[str, Any] for forward-compat: today
the .gsq codec emits xyz/quat/rgb/opacity/scales arrays; tomorrow a
SPZ-style codec might emit SH coefficients. Concrete impls type-check
their own keys.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from typing import (
    Any,
    BinaryIO,
    Protocol,
    runtime_checkable,
)

# Note: TYPE_CHECKING avoids a real import cycle with observability — at
# runtime the parameter is duck-typed.
from gsfluent.protocols.observability import EventEmitter


class CodecError(Exception):
    """Base for cache-codec errors."""


class CodecUnsanitizableError(CodecError):
    """Frame data could not be sanitized to encodable form (e.g. all-NaN xyz)."""


SplatFrame = dict[str, Any]
"""One frame's worth of splat data, as named arrays.

Standard keys (when present):
    xyz       : np.ndarray (N, 3) float32
    quat      : np.ndarray (N, 4) float32  (w, x, y, z)
    rgb       : np.ndarray (N, 3) float32  (frame 0 only for static-attrs codecs)
    opacity   : np.ndarray (N,)   float32  (frame 0 only)
    scales    : np.ndarray (N, 3) float32  (frame 0 only)
"""


@dataclass(frozen=True)
class DecodedFrame:
    """One frame's worth of decoded splat data, indexed for playback."""
    frame_index: int
    data: dict[str, Any]


@dataclass(frozen=True)
class CacheMetadata:
    """Returned by CacheCodec.encode(); summary of the encoded sequence."""
    n_splats: int
    n_frames: int
    bbox: tuple[float, float, float, float, float, float]  # xmin..zmax
    fps_hint: float = 24.0


@runtime_checkable
class CacheCodec(Protocol):
    """Encode/decode a sequence of splat frames.

    Concrete impls declare media_type + file_extension for HTTP serving.
    """

    media_type: str
    file_extension: str

    def encode(
        self,
        frames: Iterable[SplatFrame],
        out: BinaryIO,
        on_event: EventEmitter,
    ) -> CacheMetadata:
        """Encode the sequence to out. Emits structured progress events.
        Raises CodecError on unsanitizable input."""
        ...

    async def decode_streaming(
        self, src: AsyncIterator[bytes]
    ) -> AsyncIterator[DecodedFrame]:
        """Decode-as-bytes-arrive. Yields one DecodedFrame per available frame."""
        ...

    def decode_all(self, src: BinaryIO) -> Sequence[DecodedFrame]:
        """Synchronous load (used by load-from-disk path)."""
        ...
