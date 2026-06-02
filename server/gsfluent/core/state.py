"""Run state persistence — one JSON file per run under work/_state/runs/.

Atomic writes via temp-file + rename. is_pid_alive_with_starttime() defends
against PID reuse during boot recovery by cross-checking /proc/<pid>/stat
field 22 (process start time).
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from gsfluent.protocols.runs import TERMINAL_RUN_STATES, RunState


@dataclass(frozen=True)
class RunStateRecord:
    """Persisted snapshot of a run. Lives at work/_state/runs/<id>.json."""

    id: str
    state: RunState
    recipe_hash: str | None = None
    sequence_name: str | None = None
    pid: int | None = None
    pgid: int | None = None
    pid_starttime: float | None = None  # /proc/<pid>/stat field 22
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: dict[str, Any] | None = None
    paths: dict[str, str] = field(default_factory=dict)

    def transition(self, **changes: Any) -> RunStateRecord:
        return replace(self, **changes)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_RUN_STATES

    def to_json(self) -> str:
        d = asdict(self)
        d["state"] = self.state.value  # serialize enum as string
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> RunStateRecord:
        d = json.loads(raw)
        d["state"] = RunState(d["state"])
        return cls(**d)


class RunStateStore:
    """Filesystem-backed store for RunStateRecord. One JSON file per record."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        # Defensive: reject path-traversal attempts. Run IDs should be opaque
        # tokens (ULIDs); anything with a '/' or '..' is suspicious.
        if "/" in run_id or ".." in run_id or run_id.startswith("."):
            raise ValueError(f"unsafe run_id: {run_id!r}")
        return self._dir / f"{run_id}.json"

    def write(self, record: RunStateRecord) -> None:
        """Atomic write: temp file + rename.

        Re-creates the store dir first so a write self-heals if the dir was
        removed out from under a running process (e.g. a data cleanup) —
        __init__ alone can't guarantee it still exists at write time.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._path(record.id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(record.to_json())
        tmp.replace(target)

    def read(self, run_id: str) -> RunStateRecord | None:
        try:
            raw = self._path(run_id).read_text()
        except FileNotFoundError:
            return None
        return RunStateRecord.from_json(raw)

    def scan(self) -> Iterator[RunStateRecord]:
        """Yield all records in the store. Skips non-JSON files silently.

        A missing store dir yields nothing rather than raising — the dir can
        vanish under a running process (data cleanup), and a read should not
        500 the callers (list_active / history / health) when it does.
        """
        if not self._dir.is_dir():
            return
        for path in sorted(self._dir.iterdir()):
            if path.suffix != ".json":
                continue
            try:
                yield RunStateRecord.from_json(path.read_text())
            except (json.JSONDecodeError, KeyError, ValueError):
                # Corrupt file. Skip and let the operator find it via
                # observability later. Phase 4 (recover_on_boot) will
                # log this as a structured warning event.
                continue


def is_pid_alive_with_starttime(pid: int, expected_starttime: float) -> bool:
    """True iff PID is alive AND its /proc starttime matches expected.

    Defends against PID reuse during the boot-recovery window. A bare
    os.kill(pid, 0) check is insufficient: kernels reuse PIDs eagerly,
    and a fast restart can race a new process taking the old PID.

    Reads /proc/<pid>/stat field 22 (1-indexed) per `man 5 proc`. The
    `comm` field (parenthesized) is stripped before splitting because
    it may contain spaces or right-parens.

    Returns False on any read failure (process gone, permission denied,
    Linux-only /proc unavailable).
    """
    try:
        with open(f"/proc/{pid}/stat") as f:
            raw = f.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False

    try:
        # After the last ')' is the rest of the fields, space-separated.
        # Original layout: pid (comm) state ppid pgrp ... starttime ...
        # After stripping comm, field 22 (1-indexed) becomes 20 (0-indexed).
        rest = raw.rsplit(")", 1)[-1].split()
        actual_starttime = float(rest[19])
    except (IndexError, ValueError):
        return False

    # /proc starttime is in clock ticks; expected_starttime is what we
    # persisted at spawn time. Treat exact match as the contract — we
    # always read from the same /proc on the same kernel, so the units
    # agree.
    return actual_starttime == expected_starttime
