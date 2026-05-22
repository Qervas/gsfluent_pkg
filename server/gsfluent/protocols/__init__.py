"""Pure interface contracts for the six gsfluent layers.

No logic lives here — concrete implementations live under core/, storage/,
observability/, etc., and are wired in composition.py.
"""
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.storage import (
    Storage,
    StorageError,
    StorageHandle,
    StorageNotFoundError,
    StorageStat,
    StorageTransientError,
)

__all__ = [
    "EventEmitter",
    "Storage",
    "StorageError",
    "StorageHandle",
    "StorageNotFoundError",
    "StorageStat",
    "StorageTransientError",
]
