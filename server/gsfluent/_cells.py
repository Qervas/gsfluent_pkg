"""Typed reference to a library cell — replaces ad-hoc "sequence:<name>" strings.

A cell is a sequence or a model identified by a safe name. The wire form
("kind:name") is what the frontend and recipes pass around; CellRef is the
canonical in-process handle. New code should accept CellRef, not the raw
string, so the name-safety check and the kind dispatch happen once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import _paths

# Restricting to filename-safe chars stops `..`, slashes, and shell
# specials from sneaking through into FS paths and ws topics.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")

_Kind = Literal["sequence", "model"]


@dataclass(frozen=True)
class CellRef:
    kind: _Kind
    name: str

    def __post_init__(self) -> None:
        if self.kind not in ("sequence", "model"):
            raise ValueError(f"invalid cell kind: {self.kind!r}")
        if not _SAFE_NAME.match(self.name):
            raise ValueError(f"invalid cell name: {self.name!r}")

    @property
    def wire(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def gsq_path(self) -> Path:
        if self.kind != "sequence":
            raise ValueError(f"gsq_path only defined for sequences, got {self.kind!r}")
        return _paths.gsq_for(self.name)

    @property
    def library_dir(self) -> Path:
        if self.kind != "sequence":
            raise ValueError(f"library_dir only defined for sequences, got {self.kind!r}")
        return _paths.sequence_dir_for(self.name)

    @classmethod
    def parse_wire(cls, s: str) -> CellRef:
        if ":" not in s:
            raise ValueError(f"missing ':' in cell wire: {s!r}")
        kind, _, name = s.partition(":")
        if kind not in ("sequence", "model"):
            raise ValueError(f"invalid cell kind in wire: {kind!r}")
        return cls(kind=kind, name=name)  # type: ignore[arg-type]
