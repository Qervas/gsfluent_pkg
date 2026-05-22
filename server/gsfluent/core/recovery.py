"""Crash-recovery classification - pure function over a RunStateRecord.

Pulled out of AsyncioRunManager.recover_on_boot() so the decision logic
has deterministic unit tests without spawning real subprocesses.

Decision rules (spec Section 4 Flow C + Open Question 4):

  TERMINAL_ALREADY: state is in {COMPLETED, FAILED, CANCELLED, INTERRUPTED}.
                    Nothing to do - leave on disk as historical record.

  REATTACH:         state is non-terminal AND pid + pid_starttime are set
                    AND is_pid_alive_with_starttime(pid, pid_starttime)
                    returns True (PID alive AND /proc starttime matches).

  INTERRUPT:        anything else - pid missing, pid_starttime missing,
                    PID dead, or starttime mismatch (PID reuse).
                    The caller updates the record to state=INTERRUPTED
                    with error.kind = "internal.backend_restarted".
"""
from __future__ import annotations

from enum import Enum

from gsfluent.core.state import RunStateRecord, is_pid_alive_with_starttime
from gsfluent.protocols.runs import TERMINAL_RUN_STATES


class RecoveryDecision(str, Enum):
    """One of three actions classify_recovery() can return."""
    TERMINAL_ALREADY = "terminal_already"
    REATTACH = "reattach"
    INTERRUPT = "interrupt"


def classify_recovery(record: RunStateRecord) -> RecoveryDecision:
    """Decide what recover_on_boot should do with this run record."""
    if record.state in TERMINAL_RUN_STATES:
        return RecoveryDecision.TERMINAL_ALREADY

    # Non-terminal record. Need pid + pid_starttime to safely re-attach.
    if record.pid is None or record.pid_starttime is None:
        return RecoveryDecision.INTERRUPT

    if is_pid_alive_with_starttime(record.pid, record.pid_starttime):
        return RecoveryDecision.REATTACH

    return RecoveryDecision.INTERRUPT
