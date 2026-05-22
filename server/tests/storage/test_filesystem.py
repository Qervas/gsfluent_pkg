"""FilesystemStorage-specific unit tests.

Conformance against the Storage Protocol is run separately in
tests/protocols/test_storage_protocol.py (parametrized over impls).
This file covers FilesystemStorage-only concerns: path-traversal
defense, atomic rename behavior, and byte-range correctness on real files.
"""
import io
from pathlib import Path

import pytest

from gsfluent.protocols.storage import (
    Storage,
    StorageNotFoundError,
)
from gsfluent.storage.filesystem import FilesystemStorage


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemStorage:
    return FilesystemStorage(root=tmp_path)


def test_storage_satisfies_protocol(storage: FilesystemStorage) -> None:
    s: Storage = storage
    assert isinstance(s, Storage)


@pytest.mark.asyncio
async def test_put_writes_file_under_root(storage: FilesystemStorage, tmp_path: Path) -> None:
    handle = await storage.put("a.gsq", io.BytesIO(b"hello"), {})
    assert handle.key == "a.gsq"
    assert handle.size == 5
    assert (tmp_path / "a.gsq").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_put_creates_intermediate_dirs(storage: FilesystemStorage, tmp_path: Path) -> None:
    await storage.put("nested/dir/a.gsq", io.BytesIO(b"hi"), {})
    assert (tmp_path / "nested" / "dir" / "a.gsq").read_bytes() == b"hi"


@pytest.mark.asyncio
async def test_put_is_atomic(storage: FilesystemStorage, tmp_path: Path) -> None:
    """A .tmp file should not remain after a successful put."""
    await storage.put("a.gsq", io.BytesIO(b"x" * 100), {})
    assert not (tmp_path / "a.gsq.tmp").exists()


@pytest.mark.asyncio
async def test_put_rejects_absolute_key(storage: FilesystemStorage) -> None:
    with pytest.raises(ValueError):
        await storage.put("/etc/passwd", io.BytesIO(b"nope"), {})


@pytest.mark.asyncio
async def test_put_rejects_dotdot_traversal(storage: FilesystemStorage) -> None:
    with pytest.raises(ValueError):
        await storage.put("../escape.gsq", io.BytesIO(b"nope"), {})


@pytest.mark.asyncio
async def test_put_rejects_dotdot_in_middle(storage: FilesystemStorage) -> None:
    with pytest.raises(ValueError):
        await storage.put("a/../../escape.gsq", io.BytesIO(b"nope"), {})


@pytest.mark.asyncio
async def test_get_streams_file_bytes(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"hello world"), {})
    chunks = [chunk async for chunk in await storage.get("a.gsq")]
    assert b"".join(chunks) == b"hello world"


@pytest.mark.asyncio
async def test_get_raises_not_found(storage: FilesystemStorage) -> None:
    with pytest.raises(StorageNotFoundError):
        async for _ in await storage.get("missing.gsq"):
            pass


@pytest.mark.asyncio
async def test_get_range_returns_subset(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [chunk async for chunk in await storage.get_range("a.gsq", 3, 7)]
    assert b"".join(chunks) == b"3456"


@pytest.mark.asyncio
async def test_get_range_end_none_means_to_eof(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [chunk async for chunk in await storage.get_range("a.gsq", 5, None)]
    assert b"".join(chunks) == b"56789"


@pytest.mark.asyncio
async def test_get_range_zero_start(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [chunk async for chunk in await storage.get_range("a.gsq", 0, 4)]
    assert b"".join(chunks) == b"0123"


@pytest.mark.asyncio
async def test_get_range_raises_not_found(storage: FilesystemStorage) -> None:
    with pytest.raises(StorageNotFoundError):
        async for _ in await storage.get_range("missing.gsq", 0, 10):
            pass


@pytest.mark.asyncio
async def test_stat_returns_size_and_etag(storage: FilesystemStorage) -> None:
    await storage.put("a.gsq", io.BytesIO(b"hello"), {})
    st = await storage.stat("a.gsq")
    assert st is not None
    assert st.size == 5
    assert st.etag.startswith('"5-')
    assert st.etag.endswith('"')
    assert st.mtime > 0


@pytest.mark.asyncio
async def test_stat_returns_none_for_missing(storage: FilesystemStorage) -> None:
    assert (await storage.stat("missing.gsq")) is None


@pytest.mark.asyncio
async def test_exists_reflects_put(storage: FilesystemStorage) -> None:
    assert (await storage.exists("a.gsq")) is False
    await storage.put("a.gsq", io.BytesIO(b"x"), {})
    assert (await storage.exists("a.gsq")) is True


@pytest.mark.asyncio
async def test_stat_returns_none_on_traversal_key(storage: FilesystemStorage) -> None:
    """stat() never raises — even on a traversal-shaped key, it returns None
    rather than throwing. exists() likewise."""
    assert (await storage.stat("../etc/passwd")) is None
    assert (await storage.exists("../etc/passwd")) is False


@pytest.mark.asyncio
async def test_put_streams_large_payload(storage: FilesystemStorage, tmp_path: Path) -> None:
    """1 MiB payload should chunk through without OOM."""
    payload = b"x" * (1024 * 1024)
    await storage.put("big.gsq", io.BytesIO(payload), {})
    assert (tmp_path / "big.gsq").stat().st_size == 1024 * 1024
