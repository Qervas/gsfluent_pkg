"""Integration test: SIGTERM-ignoring sim gets SIGKILL after grace.

mock_sim.sh accepts MOCK_SIM_IGNORE_SIGTERM=1 to trap-and-ignore TERM.
We assert the escalation ladder still wins:
  1. SIGTERM dispatched (sim ignores it)
  2. Grace period elapses with sim still alive
  3. SIGKILL dispatched (sim dies)
  4. proc.returncode == -SIGKILL
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import pytest

from gsfluent.core.run_manager import escalate_kill_pg

from .conftest import MOCK_SIM_SH


def _pg_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
async def test_sigterm_ignoring_sim_gets_sigkill_after_grace(
    tmp_path: Path,
) -> None:
    """A mock sim that traps SIGTERM still gets killed after grace_sec.

    bash's trap-and-continue inside `while sleep` is unreliable (sleep child
    receives SIGTERM and dies), so we use a tiny Python wrapper that ignores
    SIGTERM properly.
    """
    # Custom wrapper script that traps SIGTERM in Python and ignores it.
    wrapper = tmp_path / "ignore_term_wrapper.py"
    wrapper.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "while True:\n"
        "    time.sleep(0.05)\n"
    )
    wrapper.chmod(0o755)

    import sys
    from asyncio.subprocess import create_subprocess_exec as _spawn
    proc = await _spawn(
        sys.executable, str(wrapper),
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    pgid = os.getpgid(proc.pid)
    # Allow the Python interpreter to install its SIGTERM handler.
    await asyncio.sleep(0.2)
    assert _pg_alive(pgid)

    t0 = time.monotonic()
    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=0.5)
    elapsed = time.monotonic() - t0

    assert proc.returncode is not None
    # SIGKILL'd processes have returncode == -SIGKILL (=-9).
    assert proc.returncode == -signal.SIGKILL, (
        f"expected -SIGKILL (-9), got {proc.returncode}"
    )
    # We waited at least the grace period before SIGKILL fired.
    assert elapsed >= 0.5
    assert not _pg_alive(pgid)


@pytest.mark.asyncio
async def test_well_behaved_sim_exits_cleanly_on_sigterm(
    tmp_path: Path,
) -> None:
    """A mock sim that does NOT trap SIGTERM exits cleanly (no SIGKILL needed)."""
    from asyncio.subprocess import create_subprocess_exec as _spawn
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.1",
        "MOCK_SIM_IGNORE_SIGTERM": "0",
    }
    proc = await _spawn(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "well_behaved_test",
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

    # Exited via SIGTERM (rc == -SIGTERM == -15), not SIGKILL.
    assert proc.returncode in (-signal.SIGTERM, 143)
