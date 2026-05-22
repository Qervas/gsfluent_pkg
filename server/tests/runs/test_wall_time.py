"""Tests for wall-time enforcement in AsyncioRunManager.

A run that exceeds wall_time_sec must be killed via the same PG-signal
ladder (SIGTERM -> grace -> SIGKILL) and surface as SimWallTimeExceededError.
"""
import asyncio
from pathlib import Path

import pytest

from gsfluent.core.run_manager import run_with_wall_time
from gsfluent.protocols.sim import SimWallTimeExceededError


@pytest.mark.asyncio
async def test_run_with_wall_time_returns_quickly_when_under_cap() -> None:
    """A fast task completes normally without timeout."""

    async def fast() -> str:
        await asyncio.sleep(0.01)
        return "ok"

    result = await run_with_wall_time(
        coro_factory=fast,
        wall_time_sec=5,
        on_timeout=lambda: None,
    )
    assert result == "ok"


@pytest.mark.asyncio
async def test_run_with_wall_time_raises_when_exceeded() -> None:
    """A slow task that ignores cancellation gets SimWallTimeExceededError."""

    async def slow() -> str:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            # Simulate the engine cleaning up its subprocess on cancel.
            raise
        return "should not reach"

    timeout_called = {"hit": False}

    def _on_timeout() -> None:
        timeout_called["hit"] = True

    with pytest.raises(SimWallTimeExceededError):
        await run_with_wall_time(
            coro_factory=slow,
            wall_time_sec=1,
            on_timeout=_on_timeout,
        )
    assert timeout_called["hit"] is True


@pytest.mark.asyncio
async def test_run_with_wall_time_calls_on_timeout_before_raising() -> None:
    """on_timeout fires synchronously inside the wait_for catch path."""
    call_order: list[str] = []

    async def slow() -> str:
        await asyncio.sleep(10)
        return "x"

    def _on_timeout() -> None:
        call_order.append("on_timeout")

    try:
        await run_with_wall_time(
            coro_factory=slow,
            wall_time_sec=0.2,
            on_timeout=_on_timeout,
        )
    except SimWallTimeExceededError:
        call_order.append("raised")

    assert call_order == ["on_timeout", "raised"]
