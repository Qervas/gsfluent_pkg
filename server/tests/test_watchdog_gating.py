"""Phase 6 watchdog gating: WATCHDOG=1 is suppressed when health.status == 'down'.

The watchdog heartbeat runs every WATCHDOG_INTERVAL_SEC as an in-process
asyncio task (composition._watchdog_loop). Phase 6 makes it conditional
on the in-process health snapshot: when sim_home is missing or disk is
below the operator-alert threshold, the heartbeat is suppressed so the
systemd watchdog timer fires and restarts the unit. Degraded is still
"alive but worried" — the heartbeat goes out so systemd does not restart.

These tests cover the gating function directly (pure unit) and the
in-process loop integration (drive one iteration with a fake snapshot).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from gsfluent.api.health import HealthStatus
from gsfluent.composition import (
    WATCHDOG_INTERVAL_SEC,
    _should_send_watchdog,
    _watchdog_loop,
)


def test_should_send_watchdog_true_when_ok():
    assert _should_send_watchdog(HealthStatus.OK) is True


def test_should_send_watchdog_true_when_degraded():
    """Spec contract: degraded = 'alive but worried'. Still heartbeat."""
    assert _should_send_watchdog(HealthStatus.DEGRADED) is True


def test_should_send_watchdog_false_when_down():
    """down = systemd should restart us. Suppress heartbeat."""
    assert _should_send_watchdog(HealthStatus.DOWN) is False


@pytest.mark.asyncio
async def test_watchdog_loop_emits_ping_when_status_ok(tmp_path: Path):
    """One iteration with status=ok and NOTIFY_SOCKET-mocked notify: ping seen."""
    import io

    from gsfluent.observability.jsonlog import StdlibJSONEmitter

    stream = io.StringIO()
    obs = StdlibJSONEmitter(stream=stream)

    # Patch the loop's interval down to ~0 so the test finishes fast.
    with patch("gsfluent.composition.WATCHDOG_INTERVAL_SEC", 0.01):
        with patch("gsfluent.composition._current_health_status",
                   return_value=HealthStatus.OK):
            with patch("gsfluent.composition.notify_watchdog",
                       return_value=True):
                task = asyncio.create_task(
                    _watchdog_loop(obs, health_probe=lambda: HealthStatus.OK)
                )
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    output = stream.getvalue()
    assert "backend.watchdog.ping" in output, (
        f"expected watchdog.ping in events; got: {output!r}"
    )


@pytest.mark.asyncio
async def test_watchdog_loop_emits_suppressed_when_status_down(tmp_path: Path):
    """status=down -> we record a backend.watchdog.suppressed event but skip notify."""
    import io

    from gsfluent.observability.jsonlog import StdlibJSONEmitter

    stream = io.StringIO()
    obs = StdlibJSONEmitter(stream=stream)
    notify_called = {"count": 0}

    def _fake_notify():
        notify_called["count"] += 1
        return True

    with patch("gsfluent.composition.WATCHDOG_INTERVAL_SEC", 0.01):
        with patch("gsfluent.composition.notify_watchdog",
                   side_effect=_fake_notify):
            task = asyncio.create_task(
                _watchdog_loop(obs, health_probe=lambda: HealthStatus.DOWN)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    output = stream.getvalue()
    assert "backend.watchdog.suppressed" in output, (
        f"expected watchdog.suppressed in events; got: {output!r}"
    )
    assert notify_called["count"] == 0, (
        "notify_watchdog must NOT be called when status=='down'; "
        f"called {notify_called['count']} times"
    )


@pytest.mark.asyncio
async def test_watchdog_loop_emits_ping_when_status_degraded(tmp_path: Path):
    """status=degraded -> ping is still sent (alive but worried)."""
    import io

    from gsfluent.observability.jsonlog import StdlibJSONEmitter

    stream = io.StringIO()
    obs = StdlibJSONEmitter(stream=stream)
    notify_called = {"count": 0}

    def _fake_notify():
        notify_called["count"] += 1
        return True

    with patch("gsfluent.composition.WATCHDOG_INTERVAL_SEC", 0.01):
        with patch("gsfluent.composition.notify_watchdog",
                   side_effect=_fake_notify):
            task = asyncio.create_task(
                _watchdog_loop(obs, health_probe=lambda: HealthStatus.DEGRADED)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert notify_called["count"] >= 1, (
        f"notify_watchdog must be called for status=='degraded'; "
        f"called {notify_called['count']} times"
    )
