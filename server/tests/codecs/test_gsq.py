"""GSQ codec-specific unit tests. Protocol conformance tests live in
tests/protocols/test_cache_protocol.py (parametrized over impls).
"""
import io
import struct
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.codecs.gsq import (
    HEADER_SIZE,
    INDEX_ENTRY_SIZE,
    MAGIC,
    GSQCodec,
)
from gsfluent.protocols.cache import (
    CacheCodec,
    CacheMetadata,
    CodecError,
    CodecUnsanitizableError,
)


class _NullEmitter:
    """Inline emitter — drops events."""
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


def _write_full_3dgs_frame(path: Path, n: int = 10, *, seed: int = 0) -> None:
    """Generate a tiny synthetic full 3DGS .ply at `path`."""
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
    verts["f_dc_0"] = 0.0
    verts["f_dc_1"] = 0.0
    verts["f_dc_2"] = 0.0
    verts["opacity"] = 1.0
    verts["scale_0"] = -1.0
    verts["scale_1"] = -1.0
    verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    verts["rot_1"] = 0.0
    verts["rot_2"] = 0.0
    verts["rot_3"] = 0.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


def test_codec_satisfies_protocol() -> None:
    c: CacheCodec = GSQCodec()
    assert isinstance(c, CacheCodec)


def test_codec_advertises_media_type_and_extension() -> None:
    c = GSQCodec()
    assert c.media_type == "application/x-gsq"
    assert c.file_extension == ".gsq"


def test_encode_from_frames_dir_writes_gsq_header(tmp_path: Path) -> None:
    """encode_sequence_dir generates a real .gsq with the right MAGIC."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    for i in range(3):
        _write_full_3dgs_frame(seq_dir / f"frame_{i:04d}.ply", n=4, seed=i)

    out_path = tmp_path / "demo.gsq"
    codec = GSQCodec()
    meta = codec.encode_sequence_dir(seq_dir, out_path, on_event=_NullEmitter())
    assert isinstance(meta, CacheMetadata)
    assert meta.n_frames == 3
    assert meta.n_splats == 4

    body = out_path.read_bytes()
    assert body[:4] == MAGIC
    version, n_splats, n_frames = struct.unpack("<III", body[4:16])
    assert version == 1
    assert n_splats == 4
    assert n_frames == 3


def test_encode_empty_dir_raises(tmp_path: Path) -> None:
    seq_dir = tmp_path / "empty" / "frames"
    seq_dir.mkdir(parents=True)
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(seq_dir, tmp_path / "empty.gsq", on_event=_NullEmitter())


def test_encode_missing_dir_raises(tmp_path: Path) -> None:
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(
            tmp_path / "no_such_dir", tmp_path / "x.gsq", on_event=_NullEmitter(),
        )


def test_encode_writes_index_entries_at_correct_offset(tmp_path: Path) -> None:
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    for i in range(2):
        _write_full_3dgs_frame(seq_dir / f"frame_{i:04d}.ply", n=4, seed=i)
    out_path = tmp_path / "demo.gsq"
    codec = GSQCodec()
    codec.encode_sequence_dir(seq_dir, out_path, on_event=_NullEmitter())

    body = out_path.read_bytes()
    # Header is 80 bytes; index entries follow.
    # Each entry: <QII> = 8 + 4 + 4 = 16 bytes.
    entry0 = body[HEADER_SIZE:HEADER_SIZE + INDEX_ENTRY_SIZE]
    off0, sz0, _flags = struct.unpack("<QII", entry0)
    # First frame should start AFTER the index + static block.
    # Simpler check: off0 must be > HEADER_SIZE + 2 * INDEX_ENTRY_SIZE.
    assert off0 > HEADER_SIZE + 2 * INDEX_ENTRY_SIZE


def test_encode_sanitizes_non_finite_positions(tmp_path: Path) -> None:
    """A frame with NaN positions encodes successfully (forward-filled)."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    _write_full_3dgs_frame(seq_dir / "frame_0000.ply", n=4, seed=0)
    # Frame 1 with NaN x coord. plyfile memmap-on-read requires .copy()
    # before in-place mutation to avoid a bus error on rewrite.
    _write_full_3dgs_frame(seq_dir / "frame_0001.ply", n=4, seed=1)
    bad = PlyData.read(str(seq_dir / "frame_0001.ply"))
    arr = bad["vertex"].data.copy()
    arr["x"][0] = np.nan
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(
        seq_dir / "frame_0001.ply"
    )
    codec = GSQCodec()
    # Should not raise.
    meta = codec.encode_sequence_dir(
        seq_dir, tmp_path / "demo.gsq", on_event=_NullEmitter(),
    )
    assert meta.n_frames == 2


def test_encode_all_nan_frame_raises(tmp_path: Path) -> None:
    """If every position is NaN even after forward-fill, encode raises CodecError."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    _write_full_3dgs_frame(seq_dir / "frame_0000.ply", n=4, seed=0)
    arr = PlyData.read(str(seq_dir / "frame_0000.ply"))["vertex"].data.copy()
    arr["x"][:] = np.nan
    arr["y"][:] = np.nan
    arr["z"][:] = np.nan
    PlyData([PlyElement.describe(arr, "vertex")], text=False).write(
        seq_dir / "frame_0000.ply"
    )
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(
            seq_dir, tmp_path / "demo.gsq", on_event=_NullEmitter(),
        )


def test_encode_frame_count_mismatch_raises(tmp_path: Path) -> None:
    """A frame with a different splat count than frame 0 raises."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    _write_full_3dgs_frame(seq_dir / "frame_0000.ply", n=4, seed=0)
    _write_full_3dgs_frame(seq_dir / "frame_0001.ply", n=5, seed=1)  # different N
    codec = GSQCodec()
    with pytest.raises(CodecError):
        codec.encode_sequence_dir(
            seq_dir, tmp_path / "demo.gsq", on_event=_NullEmitter(),
        )


def test_encode_emits_progress_events(tmp_path: Path) -> None:
    """on_event should see at least an encode.started / encode.completed pair."""
    seq_dir = tmp_path / "demo" / "frames"
    seq_dir.mkdir(parents=True)
    for i in range(2):
        _write_full_3dgs_frame(seq_dir / f"frame_{i:04d}.ply", n=4, seed=i)

    events: list[tuple[str, dict]] = []

    class _Capture:
        def emit(self, event, **ctx): events.append((event, ctx))
        def child(self, **ctx): return self

    codec = GSQCodec()
    codec.encode_sequence_dir(seq_dir, tmp_path / "demo.gsq", on_event=_Capture())
    names = {e for e, _ in events}
    assert "encode.started" in names
    assert "encode.completed" in names
