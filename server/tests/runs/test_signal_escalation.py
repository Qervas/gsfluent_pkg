"""Tests for the signal-escalation ladder in AsyncioRunManager.

The ladder:
  1. cancel / timeout -> os.killpg(pgid, SIGTERM)
  2. wait up to grace_sec for the process to exit
  3. if still alive -> os.killpg(pgid, SIGKILL)
"""
import asyncio
import os
import signal
from pathlib import Path

import pytest

from gsfluent.core.run_manager import (
    escalate_kill_pg,
    spawn_in_new_pg,
)


@pytest.mark.asyncio
async def test_spawn_in_new_pg_creates_distinct_process_group() -> None:
    """The spawned child gets a fresh process group (pgid != caller's pgid)."""
    proc = await spawn_in_new_pg(
        argv=["bash", "-c", "sleep 5"],
        cwd="/tmp",
    )
    try:
        child_pgid = os.getpgid(proc.pid)
        assert child_pgid != os.getpgid(0)  # different from this test's PG
        assert child_pgid == proc.pid       # child is leader of its own PG
    finally:
        try:
            os.killpg(child_pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


@pytest.mark.asyncio
async def test_escalate_kill_pg_uses_sigterm_when_child_exits_promptly() -> None:
    """A well-behaved child (exits cleanly on SIGTERM) should not get SIGKILL'd."""
    proc = await spawn_in_new_pg(argv=["bash", "-c", "sleep 30"], cwd="/tmp")
    pgid = os.getpgid(proc.pid)
    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=2.0)
    # The process should have exited via SIGTERM, returncode -SIGTERM.
    assert proc.returncode is not None
    assert proc.returncode in (-signal.SIGTERM, 143)


@pytest.mark.asyncio
async def test_escalate_kill_pg_falls_through_to_sigkill_when_sigterm_ignored(
    tmp_path: Path,
) -> None:
    """A child that traps and ignores SIGTERM gets SIGKILL after grace_sec."""
    # Write a tiny Python script that traps SIGTERM and sleeps forever.
    # Bash's trap-and-continue pattern is unreliable inside a `while sleep`
    # loop because SIGTERM reaches the sleep child too; use Python's signal
    # module which properly blocks the signal until our handler returns.
    script = tmp_path / "ignore_sigterm.py"
    script.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "while True:\n"
        "    time.sleep(0.05)\n"
    )
    script.chmod(0o755)

    import sys
    proc = await spawn_in_new_pg(argv=[sys.executable, str(script)], cwd="/tmp")
    pgid = os.getpgid(proc.pid)
    # Give the Python process time to install its SIGTERM handler before we
    # send any signal — otherwise the script may exit on SIGTERM before the
    # handler is registered, and we'd see -SIGTERM instead of -SIGKILL.
    await asyncio.sleep(0.2)
    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=0.5)
    assert proc.returncode is not None
    # SIGKILL'd processes return -9.
    assert proc.returncode == -signal.SIGKILL


@pytest.mark.asyncio
async def test_escalate_kill_pg_is_idempotent_on_already_dead_proc() -> None:
    """If the process is already dead, escalate_kill_pg should not raise."""
    proc = await spawn_in_new_pg(argv=["bash", "-c", "true"], cwd="/tmp")
    pgid = os.getpgid(proc.pid)
    await proc.wait()  # let it exit normally
    # Should not raise even though the PG is gone.
    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=0.1)
