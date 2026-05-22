"""Conformance tests for the Storage Protocol.

Any concrete Storage impl must pass these tests. Concrete impls land
in Phase 2 (FilesystemStorage). Phase 1 uses an in-memory stub to
verify the contract shape.
"""
import io
from collections.abc import AsyncIterator

import pytest

from gsfluent.protocols.storage import Storage, StorageStat


class _InMemoryStorage:
    """Stub Storage impl backed by a dict[str, bytes] — for protocol shape verification."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._mtime: dict[str, float] = {}

    async def put(self, key: str, src, metadata: dict[str, str]) -> dict:
        body = src.read()
        self._data[key] = body
        self._mtime[key] = 0.0  # deterministic for tests
        return {"key": key, "size": len(body)}

    async def get(self, key: str) -> AsyncIterator[bytes]:
        async def _gen():
            yield self._data[key]
        return _gen()

    async def get_range(self, key: str, start: int, end: int | None) -> AsyncIterator[bytes]:
        sl = self._data[key][start:end]
        async def _gen():
            yield sl
        return _gen()

    async def stat(self, key: str) -> StorageStat | None:
        if key not in self._data:
            return None
        return StorageStat(
            size=len(self._data[key]),
            mtime=self._mtime[key],
            etag=f'"{len(self._data[key])}-{int(self._mtime[key])}"',
        )

    async def exists(self, key: str) -> bool:
        return key in self._data


def test_stub_satisfies_storage_protocol() -> None:
    stub: Storage = _InMemoryStorage()
    assert isinstance(stub, Storage)


@pytest.mark.asyncio
async def test_put_then_stat_returns_size_and_etag() -> None:
    s = _InMemoryStorage()
    await s.put("a.gsq", io.BytesIO(b"abc"), {})
    st = await s.stat("a.gsq")
    assert st is not None
    assert st.size == 3
    assert st.etag.startswith('"3-')


@pytest.mark.asyncio
async def test_stat_returns_none_for_missing_key() -> None:
    s = _InMemoryStorage()
    assert (await s.stat("nope.gsq")) is None


@pytest.mark.asyncio
async def test_exists_reflects_put() -> None:
    s = _InMemoryStorage()
    assert (await s.exists("a")) is False
    await s.put("a", io.BytesIO(b"x"), {})
    assert (await s.exists("a")) is True


# --- Conformance over all real impls -----------------------------------------


@pytest.fixture
def real_filesystem_storage(tmp_path):
    from gsfluent.storage.filesystem import FilesystemStorage
    return FilesystemStorage(root=tmp_path)


def test_real_filesystem_storage_satisfies_storage_protocol(real_filesystem_storage) -> None:
    s: Storage = real_filesystem_storage
    assert isinstance(s, Storage)


@pytest.mark.asyncio
async def test_real_filesystem_storage_put_then_stat(real_filesystem_storage) -> None:
    await real_filesystem_storage.put("conf.gsq", io.BytesIO(b"abc"), {})
    st = await real_filesystem_storage.stat("conf.gsq")
    assert st is not None and st.size == 3


@pytest.mark.asyncio
async def test_real_filesystem_storage_exists(real_filesystem_storage) -> None:
    assert (await real_filesystem_storage.exists("nope.gsq")) is False
    await real_filesystem_storage.put("yes.gsq", io.BytesIO(b"x"), {})
    assert (await real_filesystem_storage.exists("yes.gsq")) is True


@pytest.mark.asyncio
async def test_real_filesystem_storage_range_round_trip(real_filesystem_storage) -> None:
    await real_filesystem_storage.put("r.gsq", io.BytesIO(b"0123456789"), {})
    chunks = [c async for c in await real_filesystem_storage.get_range("r.gsq", 2, 6)]
    assert b"".join(chunks) == b"2345"
