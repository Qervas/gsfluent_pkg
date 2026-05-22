"""FilesystemStorage — Storage Protocol impl backed by a local directory tree.

Keys are POSIX-style relative paths under the configured root. Path traversal
is rejected at the put boundary (absolute paths, parent-relative segments).
Reads return None / raise StorageNotFoundError without leaking filesystem
errors.

All writes are atomic on the same filesystem (tmp + os.replace). Range reads
use seek + read in chunks; the underlying file is opened per request so
concurrent reads don't share offsets.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from gsfluent.core.library_io import atomic_write_bytes
from gsfluent.protocols.storage import (
    StorageHandle,
    StorageNotFoundError,
    StorageStat,
)

# 64 KiB read chunks — balances per-read overhead vs memory footprint.
_READ_CHUNK = 64 * 1024


class FilesystemStorage:
    """Storage backed by a local directory tree rooted at `root`.

    Keys are POSIX-style relative paths. Examples:
        "demo.gsq"
        "cache/viser/demo.gsq"

    Construction:
        storage = FilesystemStorage(root=Path("/var/lib/gsfluent/cache"))
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # ---- key validation ----

    def _resolve_safe(self, key: str) -> Path:
        """Resolve `key` to an absolute path strictly inside `self._root`.

        Raises ValueError on absolute keys or any path that escapes the root
        via .. segments. Symlink targets are NOT followed during validation
        (resolve(strict=False) is used so we can compute the target path even
        if the file doesn't exist yet for put()).
        """
        if not key:
            raise ValueError("key must not be empty")
        # Reject absolute keys outright. PurePosixPath('/foo').is_absolute() is True.
        if key.startswith("/") or key.startswith("\\"):
            raise ValueError(f"key must not be absolute: {key!r}")
        # Reject any path component that's exactly ".." — even if it normalizes
        # back inside the root, it's a code smell that a Storage consumer is
        # constructing keys from user input without sanitizing.
        parts = key.replace("\\", "/").split("/")
        for part in parts:
            if part == ".." or part == "":
                raise ValueError(f"key contains unsafe segment {part!r}: {key!r}")
        # Final escape check by resolved-path containment.
        target = (self._root / key).resolve(strict=False)
        try:
            target.relative_to(self._root)
        except ValueError as e:
            raise ValueError(f"key escapes storage root: {key!r}") from e
        return target

    def _try_resolve(self, key: str) -> Path | None:
        """_resolve_safe but returns None instead of raising — for stat/exists
        which must not raise on bad input per the Protocol contract."""
        try:
            return self._resolve_safe(key)
        except ValueError:
            return None

    # ---- Storage Protocol ----

    async def put(self, key: str, src: BinaryIO, metadata: dict[str, str]) -> StorageHandle:
        """Write src (stream) to `key`. metadata is currently ignored
        (filesystem has no native key/value tagging; future S3 impl will use it).
        """
        target = self._resolve_safe(key)
        # Read the full payload into memory then atomic-write. For now this is
        # adequate at our payload sizes (~50-200 MB .gsq files); a streaming
        # multipart variant lands in a future sprint if the assumption breaks.
        payload = src.read()
        atomic_write_bytes(target, payload)
        st = target.stat()
        etag = f'"{st.st_size}-{int(st.st_mtime)}"'
        return StorageHandle(key=key, size=st.st_size, etag=etag)

    async def get(self, key: str) -> AsyncIterator[bytes]:
        """Stream the whole object. Raises StorageNotFoundError if absent."""
        target = self._try_resolve(key)
        if target is None or not target.is_file():
            raise StorageNotFoundError(key)
        return self._stream_range(target, 0, None)

    async def get_range(
        self, key: str, start: int, end: int | None
    ) -> AsyncIterator[bytes]:
        """Stream a byte range [start, end). end=None means to EOF."""
        target = self._try_resolve(key)
        if target is None or not target.is_file():
            raise StorageNotFoundError(key)
        return self._stream_range(target, start, end)

    async def stat(self, key: str) -> StorageStat | None:
        target = self._try_resolve(key)
        if target is None or not target.is_file():
            return None
        st = target.stat()
        return StorageStat(
            size=st.st_size,
            mtime=st.st_mtime,
            etag=f'"{st.st_size}-{int(st.st_mtime)}"',
        )

    async def exists(self, key: str) -> bool:
        target = self._try_resolve(key)
        return target is not None and target.is_file()

    # ---- helpers ----

    @staticmethod
    def _stream_range(path: Path, start: int, end: int | None) -> AsyncIterator[bytes]:
        """Async generator yielding the [start, end) byte range from `path`.

        end=None means to EOF. Reads in `_READ_CHUNK`-sized blocks so a large
        cache file streams without loading the whole thing into RAM.
        """
        async def _gen() -> AsyncIterator[bytes]:
            with open(path, "rb") as f:
                f.seek(start)
                remaining: int | None = (end - start) if end is not None else None
                while True:
                    chunk_size = _READ_CHUNK if remaining is None else min(_READ_CHUNK, remaining)
                    if chunk_size <= 0:
                        return
                    chunk = f.read(chunk_size)
                    if not chunk:
                        return
                    yield chunk
                    if remaining is not None:
                        remaining -= len(chunk)
                        if remaining <= 0:
                            return
        return _gen()
