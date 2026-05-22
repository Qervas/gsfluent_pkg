"""Phase 2 end-to-end smoke test.

Wires together the four new concretes (Mock sim -> fuser -> codec -> storage)
and verifies the .gsq round-trips through the storage layer. This is the
spec's stated Phase 2 verification gate.

Phase 3 will replace the manual pipeline assembly here with
AsyncioRunManager.submit() driving the full path.
"""
import io
from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from gsfluent.core.codecs.gsq import MAGIC, GSQCodec
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.protocols.sim import ModelRef
from gsfluent.storage.filesystem import FilesystemStorage


class _NullEmitter:
    def emit(self, event: str, **context) -> None: pass
    def child(self, **context): return self


def _write_reference_ply(path: Path, n: int = 50, seed: int = 42) -> None:
    """Write a synthetic 3DGS reference ply matching the production schema."""
    rng = np.random.default_rng(seed)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
        ("f_dc_0", "f4"), ("f_dc_1", "f4"), ("f_dc_2", "f4"),
        ("opacity", "f4"),
        ("scale_0", "f4"), ("scale_1", "f4"), ("scale_2", "f4"),
        ("rot_0", "f4"), ("rot_1", "f4"), ("rot_2", "f4"), ("rot_3", "f4"),
    ]
    verts = np.zeros(n, dtype=fields)
    verts["x"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["y"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["z"] = rng.uniform(-1, 1, n).astype(np.float32)
    verts["opacity"] = 0.5
    verts["scale_0"] = -1.0
    verts["scale_1"] = -1.0
    verts["scale_2"] = -1.0
    verts["rot_0"] = 1.0
    PlyData([PlyElement.describe(verts, "vertex")], text=False).write(path)


@pytest.mark.asyncio
async def test_phase2_e2e_mock_sim_through_fuse_pack_cache(tmp_path: Path) -> None:
    """End-to-end: mock sim -> fuser -> codec -> storage -> readback."""
    # 1. Mock sim writes sim_*.ply.
    sim_engine = MockSimulationEngine(n_frames=4, n_particles=20, seed=0)
    sim_out = tmp_path / "sim_out"
    result = await sim_engine.run(
        recipe={},
        model=ModelRef(name="mock", path=tmp_path / "model"),
        output_dir=sim_out,
        wall_time_sec=60,
        on_event=_NullEmitter(),
    )
    assert result.n_frames == 4
    sim_frames_dir = result.frames_dir
    assert (sim_frames_dir / "sim_0000.ply").is_file()

    # 2. Fuser produces frame_*.ply.
    ref_path = tmp_path / "reference.ply"
    _write_reference_ply(ref_path, n=30, seed=42)
    fused_dir = tmp_path / "fused"
    fuser = KNNKabschFuser(k=4)
    n_fused = fuser.fuse_sequence_dir(
        reference_ply_path=ref_path,
        sim_dir=sim_frames_dir,
        out_dir=fused_dir,
    )
    assert n_fused == 4
    assert (fused_dir / "frame_0000.ply").is_file()
    assert (fused_dir / "frame_0003.ply").is_file()

    # 3. Codec encodes the fused frames to .gsq.
    gsq_path = tmp_path / "smoke.gsq"
    codec = GSQCodec()
    meta = codec.encode_sequence_dir(
        fused_dir, gsq_path, on_event=_NullEmitter(),
    )
    assert meta.n_frames == 4
    assert gsq_path.is_file()
    body = gsq_path.read_bytes()
    assert body[:4] == MAGIC

    # 4. Storage layer ingests the .gsq.
    storage_root = tmp_path / "cache_root"
    storage = FilesystemStorage(root=storage_root)
    handle = await storage.put(
        "smoke.gsq", open(gsq_path, "rb"), {"content-type": codec.media_type},
    )
    assert handle.size == gsq_path.stat().st_size
    assert handle.etag.startswith(f'"{handle.size}-')

    # 5. Stat returns the same size + etag.
    stat = await storage.stat("smoke.gsq")
    assert stat is not None
    assert stat.size == handle.size
    assert stat.etag == handle.etag

    # 6. Streamed read returns the same bytes.
    chunks = [c async for c in await storage.get("smoke.gsq")]
    assert b"".join(chunks) == body

    # 7. Byte-range read returns a subset.
    chunks = [c async for c in await storage.get_range("smoke.gsq", 0, 4)]
    assert b"".join(chunks) == MAGIC
