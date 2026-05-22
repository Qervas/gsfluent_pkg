"""Tests for classify_recovery() - pure function deciding what to do with
each persisted run record at boot time."""
import os
from pathlib import Path

import pytest

from gsfluent.core.recovery import (
    RecoveryDecision,
    classify_recovery,
)
from gsfluent.core.state import RunStateRecord
from gsfluent.protocols.runs import RunState


def _read_own_starttime() -> float:
    """Read this process's starttime from /proc - used so tests can
    exercise the "alive + matches" branch deterministically."""
    pid = os.getpid()
    with open(f"/proc/{pid}/stat") as f:
        raw = f.read()
    rest = raw.rsplit(")", 1)[-1].split()
    return float(rest[19])


def test_terminal_states_get_classified_as_terminal_already() -> None:
    for s in (RunState.COMPLETED, RunState.FAILED,
              RunState.CANCELLED, RunState.INTERRUPTED):
        rec = RunStateRecord(id="r", state=s)
        assert classify_recovery(rec) == RecoveryDecision.TERMINAL_ALREADY


def test_non_terminal_with_no_pid_is_interrupted() -> None:
    """Edge case: state file has a non-terminal state but no pid was ever
    persisted (write race between create_run_record and spawn)."""
    rec = RunStateRecord(id="r", state=RunState.QUEUED, pid=None, pgid=None)
    assert classify_recovery(rec) == RecoveryDecision.INTERRUPT


def test_non_terminal_with_dead_pid_is_interrupted() -> None:
    rec = RunStateRecord(
        id="r",
        state=RunState.RUNNING,
        pid=2**31 - 1,
        pgid=2**31 - 1,
        pid_starttime=1.0,
    )
    assert classify_recovery(rec) == RecoveryDecision.INTERRUPT


def test_non_terminal_with_alive_pid_but_wrong_starttime_is_interrupted() -> None:
    """PID-reuse defense: PID is alive but starttime doesn't match the
    persisted starttime -> not the original process."""
    pid = os.getpid()
    rec = RunStateRecord(
        id="r",
        state=RunState.RUNNING,
        pid=pid,
        pgid=pid,
        pid_starttime=0.0,  # definitely not the real starttime
    )
    assert classify_recovery(rec) == RecoveryDecision.INTERRUPT


def test_non_terminal_with_alive_pid_and_matching_starttime_is_reattached() -> None:
    pid = os.getpid()
    rec = RunStateRecord(
        id="r",
        state=RunState.RUNNING,
        pid=pid,
        pgid=pid,
        pid_starttime=_read_own_starttime(),
    )
    assert classify_recovery(rec) == RecoveryDecision.REATTACH


def test_non_terminal_missing_starttime_is_interrupted() -> None:
    """If the record was written by an older backend that didn't persist
    pid_starttime, treat as interrupted - we can't verify the PID safely."""
    rec = RunStateRecord(
        id="r",
        state=RunState.RUNNING,
        pid=os.getpid(),
        pgid=os.getpid(),
        pid_starttime=None,
    )
    assert classify_recovery(rec) == RecoveryDecision.INTERRUPT
