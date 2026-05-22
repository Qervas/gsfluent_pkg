"""Integration test: cancel a running run; the entire process group dies.

Spawns mock_sim.sh as a long-running fake sim, then issues cancel(),
and verifies:
  1. SIGTERM reaches the sim's PG
  2. The PG dies within the grace period
  3. proc.returncode reflects the signal delivery
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import pytest

from gsfluent.core.limits import CapConfig
from gsfluent.core.run_manager import (
    AsyncioRunManager,
    escalate_kill_pg,
    spawn_in_new_pg,
)
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.runs import RunState
from gsfluent.protocols.sim import ModelRef

from .conftest import MOCK_SIM_SH


def _pg_alive(pgid: int) -> bool:
    """True iff at least one process in the PG is alive (probe with signal 0)."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
async def test_cancel_sends_sigterm_to_process_group(
    tmp_path: Path,
) -> None:
    """A long-running mock sim is cancelled; its PG dies within grace."""
    # Spawn a long-running fake sim that emits 100 frames at 0.5s each
    # (50s total) so it's still alive when we cancel.
    from asyncio.subprocess import create_subprocess_exec as _spawn
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.5",
    }
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    proc = await _spawn(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "cancel_test",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    pgid = os.getpgid(proc.pid)

    # Give the child a moment to spawn frame writers.
    await asyncio.sleep(0.3)
    assert _pg_alive(pgid), "mock sim died before cancel could fire"

    # Now trigger the cancel ladder.
    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=2.0)

    assert proc.returncode is not None, "proc did not exit"
    assert not _pg_alive(pgid), "PG still alive after escalate_kill_pg"


@pytest.mark.asyncio
async def test_escalate_kill_pg_terminates_pg_with_sigterm(
    tmp_path: Path,
) -> None:
    """Well-behaved mock_sim.sh exits cleanly on SIGTERM (no SIGKILL needed)."""
    from asyncio.subprocess import create_subprocess_exec as _spawn
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.5",
        "MOCK_SIM_IGNORE_SIGTERM": "0",
    }
    proc = await _spawn(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "cancel_term_test",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    pgid = os.getpgid(proc.pid)
    await asyncio.sleep(0.3)
    assert _pg_alive(pgid)

    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=2.0)

    # SIGTERM-terminated processes have returncode == -SIGTERM (== -15)
    # or 143 (== 128 + 15) if the shell converts.
    assert proc.returncode in (-signal.SIGTERM, 143), (
        f"expected -SIGTERM or 143, got {proc.returncode}"
    )
    assert not _pg_alive(pgid)
