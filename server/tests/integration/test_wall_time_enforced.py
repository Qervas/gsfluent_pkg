"""Integration test: wall-time cap kills the sim subprocess.

A mock sim configured for 100 frames at 0.5s each (50s total) is
submitted with wall_time_sec small. The orchestrator must:
  1. fire asyncio.wait_for timeout
  2. send SIGTERM to the PG (via on_timeout callback)
  3. wait grace, then SIGKILL
  4. surface SimWallTimeExceededError
"""
from __future__ import annotations

import asyncio
import io
import os
import signal
import time
from pathlib import Path

import pytest

from gsfluent.core.limits import CapConfig
from gsfluent.core.run_manager import (
    AsyncioRunManager,
    escalate_kill_pg,
    run_with_wall_time,
    spawn_in_new_pg,
)
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.runs import RunState
from gsfluent.protocols.sim import ModelRef, SimWallTimeExceededError

from .conftest import MOCK_SIM_SH


def _pg_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
async def test_run_with_wall_time_kills_mock_sim_subprocess(tmp_path: Path) -> None:
    """A long mock sim under a small wall-time cap raises SimWallTimeExceededError
    and the on_timeout callback can deliver SIGTERM/SIGKILL via the PG ladder."""
    from asyncio.subprocess import create_subprocess_exec as _spawn
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.2",
    }

    # Spawn the long-running mock sim in its own PG.
    proc = await _spawn(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "wall_time_test",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    pgid = os.getpgid(proc.pid)

    # Wait briefly so the child is definitely alive.
    await asyncio.sleep(0.2)
    assert _pg_alive(pgid)

    # on_timeout callback fires SIGTERM to the PG and schedules SIGKILL.
    timeout_hit = {"fired": False}

    def _on_timeout() -> None:
        timeout_hit["fired"] = True
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # Schedule the SIGKILL escalation in the background.
        asyncio.create_task(
            escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=1.0)
        )

    async def _wait_for_proc() -> int:
        return await proc.wait()

    with pytest.raises(SimWallTimeExceededError):
        await run_with_wall_time(
            coro_factory=_wait_for_proc,
            wall_time_sec=0.5,
            on_timeout=_on_timeout,
        )

    assert timeout_hit["fired"] is True

    # Let the SIGTERM/SIGKILL ladder finish so we can assert death.
    for _ in range(40):
        if not _pg_alive(pgid):
            break
        await asyncio.sleep(0.1)
    assert not _pg_alive(pgid), "wall-time on_timeout failed to kill PG"


@pytest.mark.asyncio
async def test_run_with_wall_time_completes_when_under_cap(tmp_path: Path) -> None:
    """A short mock sim well under the cap completes cleanly."""
    from asyncio.subprocess import create_subprocess_exec as _spawn
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "2",
        "MOCK_SIM_DELAY_SEC": "0.0",
    }
    proc = await _spawn(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "wall_time_short",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )

    rc = await run_with_wall_time(
        coro_factory=proc.wait,
        wall_time_sec=10.0,
        on_timeout=lambda: None,
    )
    assert rc == 0
