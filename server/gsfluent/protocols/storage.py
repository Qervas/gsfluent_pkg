"""Storage Protocol — layer 5.

Persistent key-addressable byte storage. Concrete impls land in Phase 2:
FilesystemStorage (current backend), and later S3Storage/GCSStorage.

Errors are typed and live in this module so callers can catch them
without importing concrete impls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, BinaryIO, Protocol, runtime_checkable


class StorageError(Exception):
    """Base for storage-layer errors."""


class StorageNotFoundError(StorageError):
    """Key does not exist."""


class StorageTransientError(StorageError):
    """Transient I/O failure — caller may retry with backoff."""


@dataclass(frozen=True)
class StorageStat:
    """Result of Storage.stat()."""
    size: int
    mtime: float        # POSIX timestamp
    etag: str           # quoted weak ETag, e.g. '"12345-1779266297"'


@dataclass(frozen=True)
class StorageHandle:
    """Result of Storage.put()."""
    key: str
    size: int
    etag: str


@runtime_checkable
class Storage(Protocol):
    """Persistent byte storage keyed by string identifier.

    Keys are filesystem-safe relative paths. Concrete impls validate.
    """

    async def put(
        self, key: str, src: BinaryIO, metadata: dict[str, str]
    ) -> StorageHandle:
        """Write src to key. metadata is impl-defined (e.g. content-type).
        Raises StorageTransientError on transient failure."""
        ...

    async def get(self, key: str) -> AsyncIterator[bytes]:
        """Stream the whole object as chunks.
        Raises StorageNotFoundError if key absent."""
        ...

    async def get_range(
        self, key: str, start: int, end: int | None
    ) -> AsyncIterator[bytes]:
        """Stream a byte range [start, end). end=None means to EOF.
        Raises StorageNotFoundError if key absent."""
        ...

    async def stat(self, key: str) -> StorageStat | None:
        """Return size+mtime+etag, or None if key absent. Never raises."""
        ...

    async def exists(self, key: str) -> bool:
        """Cheap existence check. Never raises."""
        ...
