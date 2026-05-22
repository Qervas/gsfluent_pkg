"""Tests for run state JSON persistence + boot scanner."""
import json
import os
import time
from pathlib import Path

import pytest

from gsfluent.core.state import (
    RunStateRecord,
    RunStateStore,
    is_pid_alive_with_starttime,
)
from gsfluent.protocols.runs import RunState


@pytest.fixture
def store(tmp_path: Path) -> RunStateStore:
    return RunStateStore(state_dir=tmp_path)


def test_create_then_read_round_trips(store: RunStateStore) -> None:
    rec = RunStateRecord(
        id="run-abc",
        state=RunState.QUEUED,
        recipe_hash="sha256:deadbeef",
        sequence_name="demo_seq",
    )
    store.write(rec)
    loaded = store.read("run-abc")
    assert loaded is not None
    assert loaded.id == "run-abc"
    assert loaded.state == RunState.QUEUED
    assert loaded.recipe_hash == "sha256:deadbeef"
    assert loaded.sequence_name == "demo_seq"


def test_read_missing_returns_none(store: RunStateStore) -> None:
    assert store.read("does-not-exist") is None


def test_update_state_writes_through(store: RunStateStore) -> None:
    rec = RunStateRecord(id="run-abc", state=RunState.QUEUED)
    store.write(rec)
    rec2 = rec.transition(state=RunState.STARTED, pid=12345, pgid=12345)
    store.write(rec2)
    loaded = store.read("run-abc")
    assert loaded.state == RunState.STARTED
    assert loaded.pid == 12345
    assert loaded.pgid == 12345


def test_atomic_write_via_temp_rename(store: RunStateStore, tmp_path: Path) -> None:
    """Write should not leave a partial file readable mid-flight."""
    rec = RunStateRecord(id="run-x", state=RunState.QUEUED)
    store.write(rec)
    # The on-disk file should be valid JSON the moment it appears.
    target = tmp_path / "run-x.json"
    body = json.loads(target.read_text())
    assert body["id"] == "run-x"


def test_scan_returns_all_records(store: RunStateStore) -> None:
    for i in range(3):
        store.write(RunStateRecord(id=f"r{i}", state=RunState.QUEUED))
    records = list(store.scan())
    assert {r.id for r in records} == {"r0", "r1", "r2"}


def test_scan_skips_non_json_files(store: RunStateStore, tmp_path: Path) -> None:
    store.write(RunStateRecord(id="r0", state=RunState.QUEUED))
    (tmp_path / "README.txt").write_text("not a run")
    records = list(store.scan())
    assert [r.id for r in records] == ["r0"]


def test_terminal_record_is_recognized() -> None:
    completed = RunStateRecord(id="r", state=RunState.COMPLETED)
    queued = RunStateRecord(id="r", state=RunState.QUEUED)
    assert completed.is_terminal()
    assert not queued.is_terminal()


def test_is_pid_alive_with_starttime_handles_dead_pid() -> None:
    # PID 1 typically exists (init) but the starttime won't match a fake value.
    # Use a definitely-impossible PID to confirm dead path.
    assert is_pid_alive_with_starttime(pid=2**31 - 1, expected_starttime=1.0) is False


def test_is_pid_alive_with_starttime_handles_own_process() -> None:
    """Our own PID is alive and starttime is readable."""
    pid = os.getpid()
    # Read our actual starttime from /proc, then pass it back
    with open(f"/proc/{pid}/stat") as f:
        fields = f.read().rsplit(")", 1)[-1].split()
    # /proc/PID/stat field 22 (0-indexed 21 after the comm field): starttime
    actual_starttime = float(fields[19])  # offset because comm was stripped
    assert is_pid_alive_with_starttime(pid=pid, expected_starttime=actual_starttime) is True
