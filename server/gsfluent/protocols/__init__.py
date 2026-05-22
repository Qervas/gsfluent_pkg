"""Pure interface contracts for the six gsfluent layers.

No logic lives here — concrete implementations live under core/, storage/,
observability/, etc., and are wired in composition.py.
"""
from gsfluent.protocols.observability import EventEmitter

__all__ = ["EventEmitter"]
