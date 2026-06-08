import gzip
from pathlib import Path

import pytest
from fastapi import HTTPException

from gsfluent.api import models
from gsfluent.core import models as core_models


class _ChunkedUpload:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_read_upload_capped_rejects_body_above_limit() -> None:
    upload = _ChunkedUpload(b"x" * 11)

    with pytest.raises(HTTPException) as exc:
        await models._read_upload_capped(upload, max_bytes=10, chunk_size=4)

    assert exc.value.status_code == 413


def test_gunzip_capped_rejects_decompressed_body_above_limit() -> None:
    compressed = gzip.compress(b"ply\n" + b"x" * 20)

    with pytest.raises(HTTPException) as exc:
        models._gunzip_capped(compressed, max_bytes=10, chunk_size=4)

    assert exc.value.status_code == 413


def test_register_local_model_rejects_unsafe_directory_name(tmp_path: Path) -> None:
    model_dir = tmp_path / "bad name"
    iter_dir = model_dir / "point_cloud" / "iteration_30000"
    iter_dir.mkdir(parents=True)
    (iter_dir / "point_cloud.ply").write_bytes(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 0\nend_header\n"
    )

    with pytest.raises(ValueError, match="unsafe directory name"):
        core_models.register_local_model(model_dir)
