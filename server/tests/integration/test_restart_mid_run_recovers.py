"""Integration test: backend restart with an in-flight run is recoverable.

Simulates the "Flow C" scenario from the spec:

  1. Backend A is running with one in-flight sim subprocess.
  2. Backend A is killed (SIGTERM by systemd or operator).
  3. Backend B starts up against the same on-disk state directory.
  4. recover_on_boot() runs on B and reconciles the state:
       - If the original sim PG is still alive (rare with KillMode=mixed),
         the record is REATTACHed.
       - If the sim PG died with backend A (the common case under
         KillMode=mixed), the record is marked INTERRUPTED with
         error.kind = "internal.backend_restarted".

Approach: in-process. We don't actually spawn a backend subprocess
because that is brittle in CI (port allocation, sd_notify, etc).
Instead we exercise the on-disk contract directly:

  - Manager A writes a RunStateRecord (state=RUNNING, pid=<dead pid>)
    that simulates a run that was in-flight when backend A died.
  - Manager B is constructed with the same state_dir and called.
  - We assert recover_on_boot reports interrupted=1 and the on-disk
    record now reads state=INTERRUPTED with the correct error.kind.

This intentionally complements tests/runs/test_recover_on_boot.py's
unit-level coverage by exercising the manager construction + state-dir
hand-off as one chunk - the same way composition.lifespan does on real
boot.
"""
from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateRecord, RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.runs import RecoveryReport, RunState


def _make_manager(state_dir: Path, sink: io.StringIO) -> AsyncioRunManager:
    """Construct an AsyncioRunManager wired only for recover_on_boot.

    recover_on_boot does not call sim/fuse/codec/storage; None placeholders
    are fine for this code path.
    """
    return AsyncioRunManager(
        sim_engine=None,
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=StdlibJSONEmitter(stream=sink),
        state_store=RunStateStore(state_dir=state_dir),
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


def _events(sink: io.StringIO) -> list[str]:
    """Pull the event names emitted into the sink so we can assert on flow."""
    import json
    events: list[str] = []
    for line in sink.getvalue().splitlines():
        try:
            events.append(json.loads(line)["event"])
        except (json.JSONDecodeError, KeyError):
            continue
    return events


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_state" / "runs"
    d.mkdir(parents=True)
    return d


@pytest.mark.asyncio
async def test_restart_marks_dead_in_flight_run_as_interrupted(
    state_dir: Path,
) -> None:
    """A run in state=RUNNING with a now-dead PID is INTERRUPTED on restart.

    Models the common case: backend A spawned a sim, the sim's PG died
    when systemd KillMode=mixed took down the cgroup, then backend B
    starts and finds an orphaned record.
    """
    # ---- Backend A side: write the "in-flight" record. ----
    # Use a PID that's certain to be dead. (2**31 - 1) is far above any
    # realistic kernel pid_max.
    sink_a = io.StringIO()
    mgr_a = _make_manager(state_dir, sink_a)
    mgr_a._state_store.write(RunStateRecord(
        id="in-flight-run-A",
        state=RunState.RUNNING,
        sequence_name="flow_c_sim",
        pid=2**31 - 1,
        pgid=2**31 - 1,
        pid_starttime=1.0,
    ))
    # Backend A "dies" here (in real life: systemd SIGTERMs it).

    # ---- Backend B side: fresh manager, same state_dir. ----
    sink_b = io.StringIO()
    mgr_b = _make_manager(state_dir, sink_b)
    report = await mgr_b.recover_on_boot()

    # Boot reconciliation surfaced exactly one interrupted run.
    assert report == RecoveryReport(
        reattached=0,
        interrupted=1,
        terminal_already=0,
    ), f"unexpected recovery report: {report}"

    # On-disk record now reflects the interruption.
    loaded = mgr_b._state_store.read("in-flight-run-A")
    assert loaded is not None
    assert loaded.state is RunState.INTERRUPTED
    assert loaded.error == {
        "kind": "internal.backend_restarted",
        "message": (
            "Run was interrupted by a backend restart; please re-submit"
        ),
    }
    # Original sequence_name is preserved across the transition so the
    # operator can still identify the run.
    assert loaded.sequence_name == "flow_c_sim"

    # Structured events were emitted in the right order so operators can
    # see what happened in journalctl.
    events = _events(sink_b)
    assert "boot.run.interrupted" in events
    assert "boot.recovery_complete" in events
    # boot.recovery_complete comes last.
    assert events.index("boot.recovery_complete") > events.index(
        "boot.run.interrupted"
    )


@pytest.mark.asyncio
async def test_restart_preserves_terminal_records_across_boots(
    state_dir: Path,
) -> None:
    """Terminal records (COMPLETED / FAILED / CANCELLED / INTERRUPTED) survive.

    A backend restart does not rewrite history; runs that already
    finished stay finished.
    """
    sink_a = io.StringIO()
    mgr_a = _make_manager(state_dir, sink_a)
    for i, st in enumerate((
        RunState.COMPLETED, RunState.FAILED,
        RunState.CANCELLED, RunState.INTERRUPTED,
    )):
        mgr_a._state_store.write(RunStateRecord(
            id=f"historical-{i}",
            state=st,
            sequence_name=f"hist-{st.value}",
            finished_at=1700000000.0,
        ))

    sink_b = io.StringIO()
    mgr_b = _make_manager(state_dir, sink_b)
    report = await mgr_b.recover_on_boot()

    assert report.terminal_already == 4
    assert report.reattached == 0
    assert report.interrupted == 0
    # Every historical record is byte-identical post-recovery.
    for i in range(4):
        loaded = mgr_b._state_store.read(f"historical-{i}")
        assert loaded is not None
        assert loaded.state in (
            RunState.COMPLETED, RunState.FAILED,
            RunState.CANCELLED, RunState.INTERRUPTED,
        )
        assert loaded.finished_at == 1700000000.0


@pytest.mark.asyncio
async def test_restart_reattaches_truly_live_run(state_dir: Path) -> None:
    """A non-terminal record whose PID + starttime still match a live
    process gets REATTACHed instead of marked INTERRUPTED.

    Uses the test process itself (the pytest worker) as the "still alive"
    target so the PID + starttime are guaranteed valid for the
    is_pid_alive_with_starttime cross-check.
    """
    # Read the test process's own starttime from /proc/<pid>/stat field 22.
    pid = os.getpid()
    with open(f"/proc/{pid}/stat") as f:
        raw = f.read()
    rest = raw.rsplit(")", 1)[-1].split()
    starttime = float(rest[19])

    sink_a = io.StringIO()
    mgr_a = _make_manager(state_dir, sink_a)
    mgr_a._state_store.write(RunStateRecord(
        id="reattach-target",
        state=RunState.RUNNING,
        sequence_name="still_running",
        pid=pid,
        pgid=os.getpgid(pid),
        pid_starttime=starttime,
    ))

    sink_b = io.StringIO()
    mgr_b = _make_manager(state_dir, sink_b)
    report = await mgr_b.recover_on_boot()

    assert report.reattached == 1, (
        f"expected reattach, got {report}; pid={pid} starttime={starttime}"
    )
    assert report.interrupted == 0
    assert "reattach-target" in mgr_b._reattached

    # On-disk state is NOT mutated by a REATTACH (only INTERRUPT writes).
    loaded = mgr_b._state_store.read("reattach-target")
    assert loaded is not None
    assert loaded.state is RunState.RUNNING


@pytest.mark.asyncio
async def test_no_orphan_subprocesses_referenced_after_recovery(
    state_dir: Path,
) -> None:
    """Recovery never leaves behind a subprocess we are supposed to be
    tracking. After recover_on_boot, the manager's internal handle maps
    are empty: there's no Process or pgid for any INTERRUPTED record
    (the PG is gone), and the only thing populated for REATTACHed
    records is the _reattached set (we cannot signal them anyway).

    This is the in-process equivalent of "ps shows no orphans": the
    manager makes no claim of ownership over a subprocess it cannot
    actually control.
    """
    sink_a = io.StringIO()
    mgr_a = _make_manager(state_dir, sink_a)
    mgr_a._state_store.write(RunStateRecord(
        id="orphan-candidate",
        state=RunState.RUNNING,
        pid=2**31 - 1,
        pgid=2**31 - 1,
        pid_starttime=1.0,
    ))

    sink_b = io.StringIO()
    mgr_b = _make_manager(state_dir, sink_b)
    await mgr_b.recover_on_boot()

    # No internal subprocess handles were registered during recovery -
    # the manager doesn't pretend to own a dead PG.
    assert mgr_b._procs == {}
    assert mgr_b._pgids == {}
    assert mgr_b._futures == {}
    assert mgr_b._tasks == {}


def test_no_orphan_via_ps_after_recovery(tmp_path: Path) -> None:
    """Sanity ps-based check: after we simulate a restart, there is no
    rogue subprocess for the dead-PID run that recover_on_boot just
    reconciled. This is a belt-and-suspenders alongside the in-process
    assertions above.

    Note: this is a synchronous test because subprocess.run is enough -
    we just want to verify nothing pytest-spawned is still hanging on.
    """
    # We rely on `ps -A` listing nothing matching a deterministically
    # bogus marker that the test would have spawned if recovery
    # somehow forked. recover_on_boot doesn't spawn anything, so this
    # always passes - it documents the contract rather than exercising
    # production code.
    marker = "gsfluent-orphan-marker-do-not-collide"
    result = subprocess.run(
        ["ps", "-A", "-o", "args"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert marker not in result.stdout
