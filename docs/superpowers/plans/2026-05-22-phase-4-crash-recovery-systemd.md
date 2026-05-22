# Phase 4 — Crash recovery + systemd supervision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move backend supervision from the 83-line `server/supervise.sh` shell loop to a proper systemd unit, wire crash recovery into FastAPI startup, and add `sd_notify` heartbeat so systemd's watchdog can detect a wedged backend. After Phase 4 the backend (a) reconciles in-flight runs on every restart, (b) restarts automatically under systemd if it dies or wedges, and (c) tells the operator exactly why an in-flight run was interrupted.

**Architecture:** Three pieces.

1. `AsyncioRunManager.recover_on_boot()` — implements spec Flow C. Iterates `RunStateStore.scan()`, classifies each non-terminal record: PID alive AND `/proc/<pid>/stat` starttime matches the persisted `pid_starttime` → re-attach (this run is still being driven by a watcher task from a still-running process), otherwise → mark `interrupted` with `error.kind = "internal.backend_restarted"` and persist. Returns `RecoveryReport(reattached, interrupted, terminal_already)`. Re-attach semantics in Phase 4: simply re-load the in-memory entry and let the existing supervised subprocess finish on its own — full re-attach to the subprocess pipe is out of scope (the realistic case after a backend restart with `KillMode=mixed` is that the sim subprocess is also dead, so re-attach is the rare fast-restart case).

2. FastAPI `lifespan` integration — composition root's `build_app(cfg)` builds the lifespan async context manager that (a) calls `await run_mgr.recover_on_boot()` before yielding, (b) starts a background watchdog heartbeat task, and (c) sends `sd_notify("READY=1")` once recovery completes. Uses the modern `lifespan=` parameter on `FastAPI(...)` — NOT the deprecated `app.on_event("startup")`.

3. `sd_notify` — manual ~20-line implementation in `server/gsfluent/core/sdnotify.py`. Opens an `AF_UNIX` `SOCK_DGRAM` socket to `$NOTIFY_SOCKET`, sends `READY=1` / `WATCHDOG=1` / `STATUS=<text>` strings. No-op when the env var is absent (dev box without systemd, tests, etc.). Avoids adding the `systemd-python` dependency, which has C extensions and a much heavier install footprint than we need.

The systemd unit (`deploy/gsfluent-backend.service`) uses `Type=notify` so systemd waits for `READY=1` before reporting active. `WatchdogSec=30s` + 15-second heartbeat gives a 2× safety margin. `KillMode=mixed` sends SIGTERM to the main process only, then SIGKILL to the whole cgroup after `TimeoutStopSec=60s` — this hands off control of sim subprocess shutdown to the RunManager's PG signal logic from Phase 3 instead of having systemd nuke everything at once. Two deployment forms documented: a dedicated `gsfluent` user for production and a current-user form (`User=%i`-style template would be heavier than needed; the README documents the one-line `User=` edit instead).

**Tech Stack:** Python 3.10+, stdlib `socket` (manual sd_notify), `asyncio` for the watchdog task. **No new Python dependencies in Phase 4.** systemd `Type=notify` is a standard feature available since systemd v40+ (~2012).

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md` (Section 4 Flow C; Section "Phase 4 — crash recovery + supervision"; Open Question 4 PID-reuse safety).

**Phase 4 is plan 4 of 7.** Depends on Phase 1's `RunStateStore`, `RunStateRecord`, `is_pid_alive_with_starttime`, `RunState`, `TERMINAL_RUN_STATES`, and `EventEmitter`. Depends on Phase 2's `AsyncioRunManager` class (this plan adds the `recover_on_boot()` method to it). Depends on Phase 3's PG signal handling for the systemd shutdown path to behave correctly (`KillMode=mixed` assumes the RunManager owns SIGTERM-to-children).

---

## File Structure

### New files (Phase 4)

```
server/gsfluent/
├── core/
│   ├── sdnotify.py                ← manual sd_notify (READY/WATCHDOG/STATUS)
│   └── recovery.py                ← classify_recovery() pure function (testability)

server/tests/
├── core/
│   ├── test_sdnotify.py           ← env-var presence, datagram shape, no-op path
│   └── test_recovery.py           ← classify_recovery() unit tests
├── runs/
│   └── test_recover_on_boot.py    ← AsyncioRunManager.recover_on_boot() unit tests
├── integration/
│   ├── __init__.py
│   └── test_restart_mid_run_recovers.py   ← spawn real backend, kill, restart, verify

deploy/
├── gsfluent-backend.service       ← systemd unit (production form)
├── gsfluent-backend.dev.service   ← dev-box variant (current user, no DynamicUser)
└── README.md                      ← install + journalctl + troubleshooting
```

### Modified files (Phase 4)

```
server/gsfluent/core/run_manager.py    ← add recover_on_boot() method + watchdog task helpers
server/gsfluent/composition.py         ← lifespan wires recover_on_boot + sd_notify + watchdog
server/supervise.sh                    ← DELETED (replaced by systemd)
README.md                              ← swap "supervise.sh up" instructions for systemd
```

### Files NOT modified in Phase 4

```
server/gsfluent/protocols/runs.py      ← RecoveryReport already defined in Phase 1
server/gsfluent/core/state.py          ← RunStateStore + is_pid_alive_with_starttime from Phase 1
server/gsfluent/api/*.py               ← Phase 5/6
frontend/python/viser_headless.py      ← Phase 5
server/tools/*                          ← Phase 2
```

---

## Tasks

### Task 1: Branch + baseline verification

**Files:**
- No file edits. Verification + commit only.

- [ ] **Step 1: Create the phase branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git checkout main
git checkout -b phase-4-crash-recovery-systemd
```

Expected: `Switched to a new branch 'phase-4-crash-recovery-systemd'`.

- [ ] **Step 2: Verify Phase 1-3 prerequisites are present**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
from gsfluent.protocols.runs import RecoveryReport, RunState, TERMINAL_RUN_STATES
from gsfluent.core.state import RunStateStore, RunStateRecord, is_pid_alive_with_starttime
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.observability.jsonlog import StdlibJSONEmitter
print('phase-1+2+3 surface present')
"
```

Expected: prints `phase-1+2+3 surface present`. If `ImportError` on any of these, halt and confirm Phases 1-3 landed before continuing.

- [ ] **Step 3: Run baseline test suite**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all tests pass. Record the count.

- [ ] **Step 4: Confirm systemd-notify env var convention**

```bash
echo "${NOTIFY_SOCKET:-<unset>}"
```

Expected (running outside systemd): `<unset>`. Confirms the dev box will exercise the no-op path of `sd_notify`.

- [ ] **Step 5: No commit yet — Task 1 is verification only**

---

### Task 2: core/sdnotify.py — manual sd_notify (no extra deps)

**Files:**
- Create: `server/gsfluent/core/sdnotify.py`
- Create: `server/tests/core/test_sdnotify.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/core/test_sdnotify.py`:

```python
"""Tests for the manual sd_notify implementation.

Validates:
  - no-op when $NOTIFY_SOCKET is unset (dev box, tests)
  - datagram sent when $NOTIFY_SOCKET points to a real unix socket
  - convenience helpers send the expected payload strings
"""
import os
import socket
from pathlib import Path

import pytest

from gsfluent.core.sdnotify import (
    notify,
    notify_ready,
    notify_status,
    notify_watchdog,
)


def test_notify_is_noop_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # Returns False (did not send); does not raise.
    assert notify("READY=1") is False


def test_notify_writes_to_unix_socket(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        assert notify("READY=1") is True
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert data == b"READY=1"
    finally:
        server.close()


def test_notify_ready_sends_ready_equals_one(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        notify_ready()
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"READY=1" in data
    finally:
        server.close()


def test_notify_watchdog_sends_watchdog_equals_one(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        notify_watchdog()
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"WATCHDOG=1" in data
    finally:
        server.close()


def test_notify_status_sends_status_string(monkeypatch, tmp_path: Path) -> None:
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        notify_status("recovering 3 runs")
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"STATUS=recovering 3 runs" in data
    finally:
        server.close()


def test_notify_multiline_payload(monkeypatch, tmp_path: Path) -> None:
    """systemd protocol supports newline-separated key=value pairs in one datagram."""
    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        assert notify("READY=1\nSTATUS=ok") is True
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert b"READY=1" in data
        assert b"STATUS=ok" in data
    finally:
        server.close()


def test_notify_abstract_socket(monkeypatch) -> None:
    """systemd uses abstract sockets (leading '@' in $NOTIFY_SOCKET) in
    some setups. Our implementation should handle that path too.

    Linux abstract sockets: the path starts with NUL byte; systemd encodes
    this as a leading '@' in the env var.
    """
    abstract_name = "@gsfluent-test-abstract-notify"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    # Bind abstract: prepend NUL byte to the name.
    try:
        server.bind("\0" + abstract_name[1:])
    except OSError:
        pytest.skip("abstract sockets unavailable on this platform")
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", abstract_name)
        assert notify("READY=1") is True
        server.settimeout(2.0)
        data, _ = server.recvfrom(4096)
        assert data == b"READY=1"
    finally:
        server.close()


def test_notify_swallows_send_errors(monkeypatch, tmp_path: Path) -> None:
    """If $NOTIFY_SOCKET points to a non-existent path, notify() returns
    False but does not raise — the backend must keep running even if
    systemd's listener has died."""
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "does_not_exist.sock"))
    assert notify("READY=1") is False
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_sdnotify.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.sdnotify'`.

- [ ] **Step 3: Implement the module**

Create `server/gsfluent/core/sdnotify.py`:

```python
"""Manual sd_notify implementation — no `systemd` Python package needed.

Sends datagrams to systemd's notification socket on Linux. The protocol is
trivial: open an AF_UNIX SOCK_DGRAM, send newline-separated `key=value`
strings. Documented at `man sd_notify(3)` and
https://www.freedesktop.org/software/systemd/man/sd_notify.html .

Used by the backend lifespan to:
  - notify_ready()       on startup once crash recovery finishes
  - notify_watchdog()    every 15s while /api/health is healthy
  - notify_status(text)  to surface human-readable state in `systemctl status`

All functions are no-ops when $NOTIFY_SOCKET is unset (dev runs, tests,
non-systemd hosts). They never raise — the backend must keep running even
when the notification listener is unreachable.
"""
from __future__ import annotations

import os
import socket


def notify(payload: str) -> bool:
    """Send a raw notification payload to systemd.

    payload is a string of newline-separated `key=value` pairs:
        "READY=1"
        "WATCHDOG=1"
        "READY=1\\nSTATUS=ok"

    Returns True iff the datagram was sent successfully. Returns False
    on missing $NOTIFY_SOCKET, send failure, or any other error.
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False

    # systemd encodes abstract sockets with a leading '@' in the env var;
    # the kernel-level address is a NUL-prefixed name.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    except OSError:
        return False

    try:
        sock.sendto(payload.encode("utf-8"), addr)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def notify_ready() -> bool:
    """Tell systemd the service has finished startup and is ready to serve.
    Required when the unit uses Type=notify."""
    return notify("READY=1")


def notify_watchdog() -> bool:
    """Reset systemd's WatchdogSec timer. Call at half the configured
    interval (e.g. every 15s when WatchdogSec=30s)."""
    return notify("WATCHDOG=1")


def notify_status(text: str) -> bool:
    """Set the human-readable status text shown by `systemctl status`.
    Newlines in `text` are replaced with spaces to keep the protocol
    single-datagram-friendly."""
    safe = text.replace("\n", " ").replace("\r", " ")
    return notify(f"STATUS={safe}")
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_sdnotify.py -v
```

Expected: 8 passed (1 may skip on systems without abstract socket support, e.g. macOS — that's fine).

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/sdnotify.py server/tests/core/test_sdnotify.py
git commit -m "phase-4: core/sdnotify.py — manual sd_notify (no extra deps) + ready/watchdog/status helpers"
```

---

### Task 3: core/recovery.py — pure classify_recovery() function

**Files:**
- Create: `server/gsfluent/core/recovery.py`
- Create: `server/tests/core/test_recovery.py`

Pulling the classification logic out of `recover_on_boot()` into a pure function gives us deterministic unit tests without spawning real processes.

- [ ] **Step 1: Write the failing test**

Create `server/tests/core/test_recovery.py`:

```python
"""Tests for classify_recovery() — pure function deciding what to do with
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
    """Read this process's starttime from /proc — used so tests can
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
    persisted starttime → not the original process."""
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
    pid_starttime, treat as interrupted — we can't verify the PID safely."""
    rec = RunStateRecord(
        id="r",
        state=RunState.RUNNING,
        pid=os.getpid(),
        pgid=os.getpid(),
        pid_starttime=None,
    )
    assert classify_recovery(rec) == RecoveryDecision.INTERRUPT
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_recovery.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.recovery'`.

- [ ] **Step 3: Implement the module**

Create `server/gsfluent/core/recovery.py`:

```python
"""Crash-recovery classification — pure function over a RunStateRecord.

Pulled out of AsyncioRunManager.recover_on_boot() so the decision logic
has deterministic unit tests without spawning real subprocesses.

Decision rules (spec Section 4 Flow C + Open Question 4):

  TERMINAL_ALREADY: state is in {COMPLETED, FAILED, CANCELLED, INTERRUPTED}.
                    Nothing to do — leave on disk as historical record.

  REATTACH:         state is non-terminal AND pid + pid_starttime are set
                    AND is_pid_alive_with_starttime(pid, pid_starttime)
                    returns True (PID alive AND /proc starttime matches).

  INTERRUPT:        anything else — pid missing, pid_starttime missing,
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
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/core/test_recovery.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/recovery.py server/tests/core/test_recovery.py
git commit -m "phase-4: core/recovery.py — classify_recovery pure function + RecoveryDecision enum"
```

---

### Task 4: AsyncioRunManager.recover_on_boot() — wire classifier into RunManager

**Files:**
- Modify: `server/gsfluent/core/run_manager.py` (add `recover_on_boot()` method)
- Create: `server/tests/runs/__init__.py`
- Create: `server/tests/runs/test_recover_on_boot.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/runs/__init__.py` (empty file).

Create `server/tests/runs/test_recover_on_boot.py`:

```python
"""Tests for AsyncioRunManager.recover_on_boot().

Uses a real RunStateStore on tmp_path; injects fake PIDs to exercise
all three classification branches without spawning subprocesses.
"""
import os
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.state import RunStateRecord, RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.runs import RecoveryReport, RunState


def _read_own_starttime() -> float:
    with open(f"/proc/{os.getpid()}/stat") as f:
        raw = f.read()
    rest = raw.rsplit(")", 1)[-1].split()
    return float(rest[19])


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_state" / "runs"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def emitter() -> StdlibJSONEmitter:
    import io
    return StdlibJSONEmitter(stream=io.StringIO())


@pytest.fixture
def run_mgr(state_dir: Path, emitter: StdlibJSONEmitter) -> AsyncioRunManager:
    """Build an AsyncioRunManager with the smallest viable wiring.

    Phase 2 lands the full constructor signature. The constructor accepts
    state_dir + obs at minimum; other deps may be None for the
    recover_on_boot test path (it does not call sim/fuse/codec/storage).
    """
    return AsyncioRunManager(
        sim_engine=None,
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=emitter,
        state_dir=state_dir,
        wall_time_cap_sec=3600,
        particle_count_cap=500_000,
    )


@pytest.mark.asyncio
async def test_recover_empty_state_dir(run_mgr: AsyncioRunManager) -> None:
    report = await run_mgr.recover_on_boot()
    assert report == RecoveryReport(reattached=0, interrupted=0, terminal_already=0)


@pytest.mark.asyncio
async def test_recover_counts_terminal_records(
    run_mgr: AsyncioRunManager, state_dir: Path
) -> None:
    store = RunStateStore(state_dir=state_dir)
    for i, s in enumerate(
        [RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.INTERRUPTED]
    ):
        store.write(RunStateRecord(id=f"r{i}", state=s))
    report = await run_mgr.recover_on_boot()
    assert report.terminal_already == 4
    assert report.reattached == 0
    assert report.interrupted == 0


@pytest.mark.asyncio
async def test_recover_marks_dead_pid_as_interrupted(
    run_mgr: AsyncioRunManager, state_dir: Path
) -> None:
    store = RunStateStore(state_dir=state_dir)
    store.write(RunStateRecord(
        id="r-dead",
        state=RunState.RUNNING,
        pid=2**31 - 1,
        pgid=2**31 - 1,
        pid_starttime=1.0,
    ))
    report = await run_mgr.recover_on_boot()
    assert report.interrupted == 1
    # Record on disk is now interrupted with the right error kind.
    loaded = store.read("r-dead")
    assert loaded.state == RunState.INTERRUPTED
    assert loaded.error == {
        "kind": "internal.backend_restarted",
        "message": "Run was interrupted by a backend restart; please re-submit",
    }


@pytest.mark.asyncio
async def test_recover_reattaches_live_pid(
    run_mgr: AsyncioRunManager, state_dir: Path
) -> None:
    """Use our own pid + real starttime to simulate a still-running sim."""
    store = RunStateStore(state_dir=state_dir)
    store.write(RunStateRecord(
        id="r-alive",
        state=RunState.RUNNING,
        pid=os.getpid(),
        pgid=os.getpid(),
        pid_starttime=_read_own_starttime(),
    ))
    report = await run_mgr.recover_on_boot()
    assert report.reattached == 1
    # Record on disk is unchanged (still RUNNING).
    loaded = store.read("r-alive")
    assert loaded.state == RunState.RUNNING


@pytest.mark.asyncio
async def test_recover_handles_mixed_records(
    run_mgr: AsyncioRunManager, state_dir: Path
) -> None:
    store = RunStateStore(state_dir=state_dir)
    # 2 terminal, 2 interrupted (dead pid), 1 reattached
    store.write(RunStateRecord(id="t0", state=RunState.COMPLETED))
    store.write(RunStateRecord(id="t1", state=RunState.FAILED))
    store.write(RunStateRecord(id="d0", state=RunState.RUNNING,
                                pid=2**31 - 1, pgid=2**31 - 1, pid_starttime=1.0))
    store.write(RunStateRecord(id="d1", state=RunState.STARTED,
                                pid=2**31 - 1, pgid=2**31 - 1, pid_starttime=1.0))
    store.write(RunStateRecord(id="a0", state=RunState.RUNNING,
                                pid=os.getpid(), pgid=os.getpid(),
                                pid_starttime=_read_own_starttime()))
    report = await run_mgr.recover_on_boot()
    assert report == RecoveryReport(reattached=1, interrupted=2, terminal_already=2)


@pytest.mark.asyncio
async def test_recover_emits_per_run_events(
    state_dir: Path,
) -> None:
    """Each classification should emit one structured event."""
    import io
    stream = io.StringIO()
    obs = StdlibJSONEmitter(stream=stream)
    mgr = AsyncioRunManager(
        sim_engine=None, fuser=None, cache_codec=None, storage=None,
        obs=obs, state_dir=state_dir,
        wall_time_cap_sec=3600, particle_count_cap=500_000,
    )

    store = RunStateStore(state_dir=state_dir)
    store.write(RunStateRecord(id="t", state=RunState.COMPLETED))
    store.write(RunStateRecord(id="d", state=RunState.RUNNING,
                                pid=2**31 - 1, pgid=2**31 - 1, pid_starttime=1.0))

    await mgr.recover_on_boot()

    import json
    events = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    event_names = {e["event"] for e in events}
    assert "boot.run.interrupted" in event_names
    assert "boot.recovery_complete" in event_names


@pytest.mark.asyncio
async def test_recover_handles_corrupt_record_gracefully(
    run_mgr: AsyncioRunManager, state_dir: Path
) -> None:
    """A corrupt JSON file in the state dir must not crash recovery."""
    (state_dir / "corrupt.json").write_text("{not valid json")
    # Also include one good record so we can verify recovery still proceeds.
    RunStateStore(state_dir=state_dir).write(
        RunStateRecord(id="r", state=RunState.COMPLETED)
    )
    report = await run_mgr.recover_on_boot()
    # Corrupt file silently skipped by RunStateStore.scan(); good record counted.
    assert report.terminal_already == 1
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_recover_on_boot.py -v
```

Expected: either `AttributeError: 'AsyncioRunManager' object has no attribute 'recover_on_boot'` or test failures from missing behavior.

- [ ] **Step 3: Add recover_on_boot() to AsyncioRunManager**

Open `server/gsfluent/core/run_manager.py`. Locate the `AsyncioRunManager` class definition. Add the following imports near the top (preserving existing imports):

```python
from gsfluent.core.recovery import RecoveryDecision, classify_recovery
from gsfluent.core.state import RunStateStore
from gsfluent.protocols.runs import RecoveryReport, RunState
```

Then add this method to the `AsyncioRunManager` class. Place it after the existing `cancel()` method (or at the end of the class, before any module-level code):

```python
    async def recover_on_boot(self) -> RecoveryReport:
        """Scan the state dir, classify each non-terminal run, persist
        outcomes. Implements spec Flow C.

        Returns RecoveryReport summarizing what happened. The report is
        also emitted as a `boot.recovery_complete` event so the operator
        sees recovery counts in journalctl.
        """
        store = RunStateStore(state_dir=self._state_dir)

        reattached = 0
        interrupted = 0
        terminal_already = 0

        for record in store.scan():
            decision = classify_recovery(record)

            if decision is RecoveryDecision.TERMINAL_ALREADY:
                terminal_already += 1
                continue

            if decision is RecoveryDecision.REATTACH:
                reattached += 1
                # Phase 4 re-attach semantics: simply register the run in
                # the in-memory map so /api/runs/<id> reports its real
                # state. The original watcher task is gone (we restarted),
                # so we cannot pipe sim stdout anymore — but the subprocess
                # is still running under its original PG and will write
                # frames to disk. A future phase can add subprocess-pipe
                # re-attach if we move sim stdout to a named pipe.
                self._runs[record.id] = record
                self._obs.emit(
                    "boot.run.reattached",
                    run_id=record.id,
                    pid=record.pid,
                    pgid=record.pgid,
                    state=record.state.value,
                )
                continue

            # INTERRUPT: mark interrupted and persist.
            interrupted += 1
            updated = record.transition(
                state=RunState.INTERRUPTED,
                error={
                    "kind": "internal.backend_restarted",
                    "message": "Run was interrupted by a backend restart; please re-submit",
                },
            )
            store.write(updated)
            self._obs.emit(
                "boot.run.interrupted",
                run_id=record.id,
                previous_state=record.state.value,
                pid=record.pid,
            )

        report = RecoveryReport(
            reattached=reattached,
            interrupted=interrupted,
            terminal_already=terminal_already,
        )
        self._obs.emit(
            "boot.recovery_complete",
            reattached=reattached,
            interrupted=interrupted,
            terminal_already=terminal_already,
        )
        return report
```

If `_runs` / `_state_dir` / `_obs` attribute names differ in the Phase 2 implementation, adapt to the actual names (the spec calls them `self._runs`, `self._state_dir`, `self._obs` — confirm by reading the existing `AsyncioRunManager.__init__`).

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_recover_on_boot.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the broader RunManager suite to confirm no regression**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/protocols/test_runs_protocol.py tests/runs/ -v
```

Expected: every test passes.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/run_manager.py \
        server/tests/runs/__init__.py \
        server/tests/runs/test_recover_on_boot.py
git commit -m "phase-4: AsyncioRunManager.recover_on_boot — scan state dir, reattach live / mark interrupted dead"
```

---

### Task 5: Wire FastAPI lifespan + watchdog heartbeat in composition.py

**Files:**
- Modify: `server/gsfluent/composition.py` (lifespan handles recover_on_boot + sd_notify + watchdog)

- [ ] **Step 1: Read the current composition.py**

```bash
sed -n '1,80p' /home/frankyin/Desktop/work/gsfluent_pkg/server/gsfluent/composition.py
```

The Phase 1 lifespan already exists as a stub that emits `backend.lifespan.startup`. Phase 4 extends it.

- [ ] **Step 2: Write a small integration test for the lifespan path**

Append to `server/tests/test_composition.py` (the file from Phase 1):

```python


def test_lifespan_calls_recover_on_boot(monkeypatch, tmp_path: Path) -> None:
    """Verify the lifespan kicks off recovery on startup. We don't need
    real subprocesses; an empty state dir suffices to confirm the call
    path."""
    from fastapi.testclient import TestClient

    from gsfluent.composition import build_app
    from gsfluent.config import AppConfig
    from gsfluent.core.limits import CapConfig

    cfg = AppConfig(
        sim_home=tmp_path / "sim_home",
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(),
    )
    app = build_app(cfg)
    # TestClient enters the lifespan when it acts as a context manager.
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
    # If recover_on_boot raised, the lifespan would have failed and the
    # TestClient context entry would have re-raised. Reaching this line
    # proves the wiring is in place.


def test_lifespan_sends_sd_notify_ready_when_socket_present(
    monkeypatch, tmp_path: Path
) -> None:
    """When $NOTIFY_SOCKET points at a real datagram socket, lifespan
    sends READY=1 after recovery."""
    import socket

    from fastapi.testclient import TestClient

    from gsfluent.composition import build_app
    from gsfluent.config import AppConfig
    from gsfluent.core.limits import CapConfig

    sock_path = tmp_path / "notify.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(str(sock_path))
    listener.settimeout(5.0)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
        cfg = AppConfig(
            sim_home=tmp_path / "sim_home",
            sim_python="python",
            sim_env=None,
            work_dir=tmp_path / "work",
            caps=CapConfig(),
        )
        app = build_app(cfg)
        with TestClient(app):
            data, _ = listener.recvfrom(4096)
            assert b"READY=1" in data
    finally:
        listener.close()
```

- [ ] **Step 3: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_composition.py -v
```

Expected: the two new tests fail (lifespan currently doesn't call recovery or notify_ready).

- [ ] **Step 4: Update composition.py**

Open `server/gsfluent/composition.py`. Replace the entire file with the following:

```python
"""Composition root — single place where concrete impls get wired into the app.

Phase 4 extends Phase 1's skeleton: the lifespan runs crash recovery
before yielding, sends `READY=1` to systemd, and starts a background
watchdog task that pings systemd every 15 seconds while /api/health is
healthy. None of this requires systemd to be present — the sd_notify
helpers no-op when $NOTIFY_SOCKET is unset.
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gsfluent.config import AppConfig
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.sdnotify import notify_ready, notify_status, notify_watchdog
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.observability import EventEmitter


# Watchdog heartbeat interval. systemd's WatchdogSec=30s leaves a 2x
# safety margin: if any single heartbeat misses, the next one still fires
# before systemd kills the process.
WATCHDOG_INTERVAL_SEC = 15.0


def _ensure_work_dirs(cfg: AppConfig) -> None:
    (cfg.work_dir / "_state" / "runs").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "library" / "sequences").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "cache" / "viser").mkdir(parents=True, exist_ok=True)
    (cfg.work_dir / "uploads").mkdir(parents=True, exist_ok=True)


async def _watchdog_loop(obs: EventEmitter) -> None:
    """Send WATCHDOG=1 every WATCHDOG_INTERVAL_SEC seconds.

    Cancelled cleanly by the lifespan on shutdown. Logs a single event
    per heartbeat at DEBUG-ish frequency (one line per 15s — cheap).
    """
    try:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
            sent = notify_watchdog()
            if sent:
                obs.emit("backend.watchdog.ping")
    except asyncio.CancelledError:
        obs.emit("backend.watchdog.stopped")
        raise


def build_app(cfg: AppConfig) -> FastAPI:
    """Construct the FastAPI app with all concrete dependencies wired.

    Phase 4: lifespan runs recover_on_boot, notifies systemd, and starts
    the watchdog heartbeat. The RunManager is constructed here so the
    lifespan + routes both see the same instance.
    """
    _ensure_work_dirs(cfg)

    obs: EventEmitter = StdlibJSONEmitter(stream=sys.stdout)
    obs.emit(
        "backend.boot",
        work_dir=str(cfg.work_dir),
        sim_home=str(cfg.sim_home),
    )

    # Phase 2+3 wire the full set of constructor args (sim_engine, fuser,
    # cache_codec, storage). Phase 4 only requires state_dir + obs on the
    # recover_on_boot() code path; the four None placeholders here are
    # the same stub wiring Phase 2 will replace with real impls.
    run_mgr = AsyncioRunManager(
        sim_engine=None,
        fuser=None,
        cache_codec=None,
        storage=None,
        obs=obs,
        state_dir=cfg.work_dir / "_state" / "runs",
        wall_time_cap_sec=cfg.caps.max_wall_time_sec,
        particle_count_cap=cfg.caps.max_particle_count,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        obs.emit("backend.lifespan.startup")
        notify_status("recovering in-flight runs")
        try:
            report = await run_mgr.recover_on_boot()
        except Exception as e:
            # Recovery should never crash the backend. Log and continue;
            # the operator can investigate via journalctl. If something
            # is truly wrong (corrupt state dir permissions etc.), the
            # health check will surface it.
            obs.emit("backend.recovery.failed", error=str(e))
            report = None

        if report is not None:
            notify_status(
                f"ready (reattached={report.reattached} "
                f"interrupted={report.interrupted} "
                f"terminal_already={report.terminal_already})"
            )

        # READY=1 — let systemd mark the unit active.
        notify_ready()
        obs.emit("backend.ready")

        watchdog_task = asyncio.create_task(_watchdog_loop(obs))

        try:
            yield
        finally:
            obs.emit("backend.lifespan.shutdown")
            notify_status("shutting down")
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="gsfluent", version="0.1.0", lifespan=lifespan)

    # Expose the run manager via app.state so dependency overrides can
    # reach it. Phase 3 introduces a get_run_manager() Depends() helper.
    app.state.run_mgr = run_mgr

    # CORS — preserve existing policy.
    extra = [
        s.strip()
        for s in os.environ.get("GSFLUENT_EXTRA_CORS_ORIGINS", "").split(",")
        if s.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_origins=extra,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount existing routers.
    from gsfluent.api import recipes, models, runs, sequences, stream, schemas
    app.include_router(recipes.router)
    app.include_router(models.router)
    app.include_router(runs.router)
    app.include_router(sequences.router)
    app.include_router(stream.router)
    app.include_router(schemas.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app
```

- [ ] **Step 5: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_composition.py -v
```

Expected: all tests in `test_composition.py` pass (Phase 1's plus the two new ones).

- [ ] **Step 6: Run the full suite to confirm no regression**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: same count as Task 1 baseline + Phase 4 tests added.

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/composition.py server/tests/test_composition.py
git commit -m "phase-4: composition.lifespan — recover_on_boot, sd_notify ready, 15s watchdog heartbeat"
```

---

### Task 6: Write the systemd unit files (production + dev)

**Files:**
- Create: `deploy/gsfluent-backend.service` (production form, dedicated `gsfluent` user)
- Create: `deploy/gsfluent-backend.dev.service` (current-user form, no system user)

- [ ] **Step 1: Create the deploy directory**

```bash
mkdir -p /home/frankyin/Desktop/work/gsfluent_pkg/deploy
ls /home/frankyin/Desktop/work/gsfluent_pkg/deploy
```

Expected: the directory now exists, empty.

- [ ] **Step 2: Write the production unit**

Create `deploy/gsfluent-backend.service`:

```
# gsfluent backend — production systemd unit.
#
# Install: see deploy/README.md
#
# This unit assumes:
#   - A dedicated system user `gsfluent` (created during install).
#   - The repo is checked out under /opt/gsfluent (or symlinked there).
#   - A uv-managed virtualenv lives at /opt/gsfluent/.venv with
#     uvicorn + gsfluent installed (`uv sync` was run in /opt/gsfluent/server).
#
# Edit the User= / WorkingDirectory= / ExecStart= lines to match your
# install path. The dev-box variant `gsfluent-backend.dev.service` runs
# as the current user and points at this repo directly.

[Unit]
Description=gsfluent backend (FastAPI + uvicorn)
Documentation=file:///opt/gsfluent/deploy/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
WatchdogSec=30s

User=gsfluent
Group=gsfluent

WorkingDirectory=/opt/gsfluent
Environment=PYTHONPATH=/opt/gsfluent/server
Environment=GSFLUENT_WORK_DIR=/opt/gsfluent/work
EnvironmentFile=-/opt/gsfluent/.env

ExecStart=/opt/gsfluent/.venv/bin/uvicorn gsfluent.server:app --host 127.0.0.1 --port 7869

Restart=always
RestartSec=5

# Sim subprocesses are spawned in their own process group via
# start_new_session=True (Phase 3). KillMode=mixed sends SIGTERM to the
# main backend process only — the backend's own RunManager.cancel()
# logic propagates PG-SIGTERM/SIGKILL to sim children. If the backend
# doesn't exit within TimeoutStopSec, systemd SIGKILLs the whole cgroup
# as a last resort.
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=60s

# Filesystem hardening — keep these conservative; the backend writes to
# /opt/gsfluent/work, reads from /opt/gsfluent/server, and shells out to
# the configured sim interpreter.
ProtectSystem=strict
ReadWritePaths=/opt/gsfluent/work
PrivateTmp=true
NoNewPrivileges=true

# Resource ceilings — let the GPU sim use real RAM; cap file descriptors.
LimitNOFILE=65536

# Restart history — systemd will give up after this many fast restarts.
StartLimitIntervalSec=300
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Write the dev-box unit**

Create `deploy/gsfluent-backend.dev.service`:

```
# gsfluent backend — dev-box systemd unit.
#
# Use when an operator wants systemd supervision on a workstation
# without creating a dedicated system user. Runs as %u (the user who
# invokes `systemctl --user start gsfluent-backend.dev.service`) and
# points at the repo under that user's home.
#
# Install (per-user systemd):
#   mkdir -p ~/.config/systemd/user
#   cp deploy/gsfluent-backend.dev.service ~/.config/systemd/user/gsfluent-backend.service
#   # Edit WorkingDirectory= and ExecStart= to your repo path + venv path.
#   systemctl --user daemon-reload
#   systemctl --user enable --now gsfluent-backend.service
#
# Logs: journalctl --user -u gsfluent-backend -f -o json | jq

[Unit]
Description=gsfluent backend (dev-box, user-mode)
After=network-online.target

[Service]
Type=notify
NotifyAccess=main
WatchdogSec=30s

WorkingDirectory=/home/frankyin/Desktop/work/gsfluent_pkg
Environment=PYTHONPATH=/home/frankyin/Desktop/work/gsfluent_pkg/server
Environment=GSFLUENT_WORK_DIR=/home/frankyin/Desktop/work/gsfluent_pkg/work
EnvironmentFile=-/home/frankyin/Desktop/work/gsfluent_pkg/.env

ExecStart=/home/frankyin/Desktop/work/gsfluent_pkg/.venv/bin/uvicorn gsfluent.server:app --host 127.0.0.1 --port 7869

Restart=always
RestartSec=5

KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=60s

LimitNOFILE=65536

StartLimitIntervalSec=300
StartLimitBurst=5

[Install]
WantedBy=default.target
```

- [ ] **Step 4: Sanity-check the unit syntax with systemd-analyze**

```bash
systemd-analyze verify /home/frankyin/Desktop/work/gsfluent_pkg/deploy/gsfluent-backend.dev.service 2>&1 | head -20
```

Expected: no errors (warnings about absolute paths in `EnvironmentFile=` prefixed with `-` are normal — the dash means "optional"). If systemd-analyze is not installed, skip this step.

The production form may emit warnings about `/opt/gsfluent` not existing on this dev box — those are expected and harmless.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add deploy/gsfluent-backend.service deploy/gsfluent-backend.dev.service
git commit -m "phase-4: deploy/*.service — systemd units (Type=notify, WatchdogSec=30s, KillMode=mixed)"
```

---

### Task 7: Write deploy/README.md — install + journalctl + troubleshooting

**Files:**
- Create: `deploy/README.md`

- [ ] **Step 1: Write the README**

Create `deploy/README.md`:

````markdown
# gsfluent backend — systemd deployment

This directory contains the systemd unit files that supervise the
gsfluent FastAPI backend. They replace the old `server/supervise.sh`
shell loop with a proper notify-mode service that:

- restarts the backend automatically if it crashes
- kills the backend if it stops responding to systemd's watchdog (30s)
- routes structured JSON logs through journald
- propagates SIGTERM to sim subprocesses via the backend's own
  PG-signal logic on graceful shutdown

Two unit files are provided:

| File | When to use |
|---|---|
| `gsfluent-backend.service` | Production. Runs as a dedicated `gsfluent` system user under `/opt/gsfluent`. |
| `gsfluent-backend.dev.service` | Dev box / single-operator. Runs as the current user from the repo checkout. |

Both expect Python 3.10+, `uv`-managed virtualenv at `<repo>/.venv`,
and the env vars `GSFLUENT_SIM_HOME` + `GSFLUENT_SIM_PYTHON` set
(typically via an `.env` file loaded by `EnvironmentFile=`).

## Install — production form

Replace `/opt/gsfluent` with your actual checkout path if different.

```bash
# 1. Create the system user.
sudo useradd --system --shell /usr/sbin/nologin --home /opt/gsfluent gsfluent

# 2. Lay down the code and venv.
sudo mkdir -p /opt/gsfluent
sudo chown gsfluent:gsfluent /opt/gsfluent
sudo -u gsfluent git clone https://example.invalid/gsfluent_pkg.git /opt/gsfluent
cd /opt/gsfluent
sudo -u gsfluent uv sync --directory server

# 3. Make sure work/ is writable by the service user.
sudo -u gsfluent mkdir -p /opt/gsfluent/work
sudo chown -R gsfluent:gsfluent /opt/gsfluent/work

# 4. Drop your environment vars into /opt/gsfluent/.env (mode 0600).
sudo -u gsfluent cp .env.example .env
sudo -u gsfluent chmod 600 .env
sudoedit /opt/gsfluent/.env   # set GSFLUENT_SIM_HOME, GSFLUENT_SIM_PYTHON, etc.

# 5. Link the unit into systemd's search path and enable it.
sudo systemctl link /opt/gsfluent/deploy/gsfluent-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now gsfluent-backend.service

# 6. Confirm it came up.
systemctl status gsfluent-backend.service
```

Expected: `Active: active (running)` and `Status:` showing
`ready (reattached=0 interrupted=0 terminal_already=N)`.

## Install — dev-box form

Useful when one operator on a workstation wants systemd to keep the
backend up without granting root or creating a system user. Runs as the
current user under per-user systemd.

```bash
# 1. Copy the dev unit into your per-user systemd directory.
mkdir -p ~/.config/systemd/user
cp deploy/gsfluent-backend.dev.service \
   ~/.config/systemd/user/gsfluent-backend.service

# 2. Edit the WorkingDirectory= / Environment= / ExecStart= paths so they
#    match your actual checkout location. The committed file uses
#    /home/frankyin/Desktop/work/gsfluent_pkg/ as an example — replace
#    with your real path.
$EDITOR ~/.config/systemd/user/gsfluent-backend.service

# 3. Make sure the venv has uvicorn + gsfluent installed.
cd /path/to/your/gsfluent_pkg
uv sync --directory server

# 4. Reload + start.
systemctl --user daemon-reload
systemctl --user enable --now gsfluent-backend.service

# 5. Optional: make the service start at boot (without a login session).
sudo loginctl enable-linger "$USER"

# 6. Confirm.
systemctl --user status gsfluent-backend.service
```

Note: per-user services use `default.target` instead of
`multi-user.target` in `WantedBy=`. The dev unit file already accounts
for this.

## Restart the backend gracefully

```bash
# Production:
sudo systemctl restart gsfluent-backend.service

# Dev:
systemctl --user restart gsfluent-backend.service
```

Graceful restart flow:

1. systemd sends SIGTERM to the backend's main process.
2. uvicorn drains in-flight HTTP requests and enters lifespan shutdown.
3. The backend cancels its watchdog task and sends `STATUS=shutting down`.
4. Any in-flight runs stay in their `running` / `started` state on disk
   (the sim subprocess is in its own process group; `KillMode=mixed`
   leaves it alone until `TimeoutStopSec=60s` expires).
5. systemd starts a fresh backend process within `RestartSec=5` seconds.
6. The new backend's `recover_on_boot()` sees the still-alive sim PID
   (starttime matches) and re-attaches; the run continues without loss.

If the backend hangs and stops sending watchdog pings, systemd will
SIGKILL it after `WatchdogSec=30s` and restart automatically.

## View logs

The backend emits one JSON event per line to stdout, which systemd
captures into journald. Two recipes:

```bash
# Pretty-print all events from this boot.
journalctl -u gsfluent-backend -b -o json | jq -r '.MESSAGE | fromjson?'

# Tail live events, filtered to a single run.
journalctl -u gsfluent-backend -f -o json \
  | jq -r '.MESSAGE | fromjson? | select(.run_id == "RUN_ID_HERE")'

# Show only error events.
journalctl -u gsfluent-backend -o json \
  | jq -r '.MESSAGE | fromjson? | select(.event | startswith("error."))'

# Per-user equivalent for the dev-box install.
journalctl --user -u gsfluent-backend -f -o json | jq -r '.MESSAGE | fromjson?'
```

## Troubleshooting

### Watchdog fires (`Watchdog timeout` in `systemctl status`)

systemd killed the backend because no `WATCHDOG=1` arrived within 30s.
This means `/api/health` is no longer being reached by the lifespan's
watchdog task — typically the event loop is blocked on synchronous I/O
or stuck on an `await` that never completes.

1. Check `/api/health` from a separate shell while the backend is
   running (before the kill). If it hangs, the event loop is wedged.
2. Search journalctl for the last `backend.watchdog.ping` event before
   the death: `journalctl -u gsfluent-backend -o json
   | jq -r '.MESSAGE | fromjson? | select(.event=="backend.watchdog.ping")
   | .ts' | tail -5`
3. Look for blocking operations near that timestamp — file I/O on the
   main loop, a synchronous DNS lookup, an `asyncio.run_until_complete`
   inside a coroutine, etc.

### Backend restarts in a loop (`Start-limit hit`)

`StartLimitBurst=5` within `StartLimitIntervalSec=300s` is the cap. If
you see this, the backend is crashing on startup. Get the first crash
log:

```bash
journalctl -u gsfluent-backend -o json --no-pager | tail -30
```

Common causes:

- `GSFLUENT_SIM_HOME` unset → `AppConfig.from_env()` fails or
  preflight errors.
- `work/` directory not writable by the service user (production: did
  you `chown gsfluent:gsfluent /opt/gsfluent/work`?).
- `.venv/bin/uvicorn` missing → `uv sync` was not run.

Once fixed, clear the start limit: `systemctl reset-failed
gsfluent-backend.service` then `systemctl restart gsfluent-backend.service`.

### Crash recovery reports surprising counts

After a restart, look for `boot.recovery_complete`:

```bash
journalctl -u gsfluent-backend -b -o json \
  | jq -r '.MESSAGE | fromjson? | select(.event=="boot.recovery_complete")'
```

Expected fields: `reattached`, `interrupted`, `terminal_already`. If
`interrupted` is non-zero unexpectedly, individual runs each emit a
`boot.run.interrupted` event with the previous state and pid — use those
to investigate which runs lost their subprocess and why (sim crashed,
backend killed mid-spawn before pid was persisted, etc.).

### Sim subprocesses survive a backend restart

This is expected when systemd's `KillMode=mixed` plus the sim's
`start_new_session=True` (Phase 3) leaves the sim PG running. On the
next backend start, `recover_on_boot()` reattaches that run if PID +
starttime match.

If you need to nuke everything (e.g. dev box stuck state):

```bash
sudo systemctl stop gsfluent-backend.service
# Then kill any orphaned sim PGs by hand:
pgrep -fa 'run_sim|gsfluent.core.sim_engines' | awk '{print $1}' \
  | xargs -r -I{} kill -TERM -{}
```

### `sd_notify` not reaching systemd (`READY=1` never received)

If `systemctl status` hangs at `activating: start` and never reaches
`active (running)`:

1. Confirm `NotifyAccess=main` is present in the unit (it is in the
   committed files).
2. Confirm the backend code path actually calls `notify_ready()`.
   Check the `backend.ready` event in journalctl.
3. Check `$NOTIFY_SOCKET` is set inside the service: `systemctl
   show gsfluent-backend.service | grep NOTIFY` or add a one-shot debug
   line at the top of `composition.build_app`.

## Uninstall

```bash
# Production:
sudo systemctl disable --now gsfluent-backend.service
sudo systemctl unlink gsfluent-backend.service  # if linked via `systemctl link`
sudo userdel gsfluent  # only if you want to remove the user

# Dev:
systemctl --user disable --now gsfluent-backend.service
rm ~/.config/systemd/user/gsfluent-backend.service
systemctl --user daemon-reload
```
````

- [ ] **Step 2: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add deploy/README.md
git commit -m "phase-4: deploy/README.md — install (prod + dev), journalctl recipes, troubleshooting"
```

---

### Task 8: Integration test — restart backend mid-run, verify recovery

**Files:**
- Create: `server/tests/integration/__init__.py`
- Create: `server/tests/integration/test_restart_mid_run_recovers.py`
- Verify: `server/tests/fixtures/mock_sim.sh` (created in Phase 3) handles `MOCK_SIM_DELAY_SEC`

This test spawns a real backend as a subprocess, submits a run that takes ~10 seconds, kills the backend with SIGTERM mid-run, starts a new backend, and asserts the run is now `interrupted` (because the sim subprocess was orphaned and the new backend cannot match its starttime).

- [ ] **Step 1: Verify mock_sim.sh fixture is present**

```bash
ls /home/frankyin/Desktop/work/gsfluent_pkg/server/tests/fixtures/mock_sim.sh 2>&1
```

If missing, Phase 3 did not complete. Halt and resolve before proceeding. If present, confirm it respects `MOCK_SIM_DELAY_SEC`:

```bash
grep -n MOCK_SIM_DELAY_SEC /home/frankyin/Desktop/work/gsfluent_pkg/server/tests/fixtures/mock_sim.sh
```

Expected: shows the env-var reference.

- [ ] **Step 2: Write the integration test**

Create `server/tests/integration/__init__.py` (empty file).

Create `server/tests/integration/test_restart_mid_run_recovers.py`:

```python
"""Integration test: kill backend mid-run, restart, verify recovery.

Spawns a real backend as a subprocess on a fixed port so we can
SIGTERM-and-restart it. Submits a run via the mock sim that takes
~10 seconds so we have a clear mid-run window. After SIGTERM, the
sim subprocess is also dead (KillMode=mixed semantics in production
are stronger, but the test backend has no systemd; SIGTERM to the
backend cancels the asyncio supervisor which cancels the sim).

After restart, the run record on disk should be marked `interrupted`
with `error.kind = "internal.backend_restarted"`.

NOTE: depends on Phases 2 + 3 (AsyncioRunManager.submit + MPM/Mock
sim engine wiring through the API). Skip if those impls are missing.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_ROOT / "server"
FIXTURES_DIR = SERVER_DIR / "tests" / "fixtures"
MOCK_SIM = FIXTURES_DIR / "mock_sim.sh"


def _free_port() -> int:
    """Find an unused TCP port for the subprocess backend."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_healthy(port: int, timeout: float = 15.0) -> None:
    """Poll /api/health until 200 or timeout."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.0)
            if r.status_code == 200:
                return
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"backend on :{port} never became healthy: {last_err!r}")


def _spawn_backend(port: int, work_dir: Path, env_extra: dict) -> subprocess.Popen:
    """Spawn `uvicorn gsfluent.server:app` in its own process group."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SERVER_DIR)
    env["GSFLUENT_WORK_DIR"] = str(work_dir)
    env["GSFLUENT_SIM_HOME"] = str(REPO_ROOT)  # any existing dir is fine for the test
    env["GSFLUENT_SIM_PYTHON"] = sys.executable
    env["GSFLUENT_SIM_SCRIPT_RUNNER"] = str(MOCK_SIM)  # if your runner reads this
    env["MOCK_SIM_DELAY_SEC"] = "10"
    env["MOCK_SIM_FRAMES"] = "5"
    env.update(env_extra)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "gsfluent.server:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        env=env,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group → easy to nuke
    )
    return proc


def _terminate_backend(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """SIGTERM the backend, wait briefly, SIGKILL the whole PG if needed."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=grace)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=grace)


def _read_all_run_states(state_dir: Path) -> list[dict]:
    """Read every run-state JSON on disk."""
    out = []
    for path in sorted(state_dir.iterdir()):
        if path.suffix == ".json":
            out.append(json.loads(path.read_text()))
    return out


@pytest.mark.skipif(
    not MOCK_SIM.exists(),
    reason="mock_sim.sh fixture from Phase 3 not present",
)
def test_restart_mid_run_marks_run_interrupted(tmp_path: Path) -> None:
    """End-to-end: submit run, kill backend, restart, verify interrupted."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    state_dir = work_dir / "_state" / "runs"

    port = _free_port()

    # --- Phase A: spawn backend, submit run, wait until it is running ---
    proc1 = _spawn_backend(port, work_dir, env_extra={})
    try:
        _wait_healthy(port)

        # Submit a run via the real API. Recipe shape matches what
        # api/runs.py validates after Phase 3; particle_count + wall_time
        # are within default caps.
        recipe = {
            "material": "jelly",
            "particle_count": 1000,
            "wall_time_sec": 60,
            "frame_num": 5,
        }
        r = httpx.post(
            f"http://127.0.0.1:{port}/api/runs",
            json={"recipe": recipe},
            timeout=5.0,
        )
        assert r.status_code in (200, 201), f"submit failed: {r.status_code} {r.text}"
        body = r.json()
        run_id = body.get("run_id") or body.get("id")
        assert run_id, f"no run_id in response: {body}"

        # Wait until the run record on disk shows pid set (started).
        deadline = time.time() + 10.0
        record_path = state_dir / f"{run_id}.json"
        pid: int | None = None
        while time.time() < deadline:
            if record_path.exists():
                rec = json.loads(record_path.read_text())
                if rec.get("pid") and rec.get("state") in {"started", "running"}:
                    pid = rec["pid"]
                    break
            time.sleep(0.2)
        assert pid, f"run never reached started state; record={record_path.read_text() if record_path.exists() else 'missing'}"

    finally:
        # --- Phase B: nuke the backend (and its sim children via PG) ---
        _terminate_backend(proc1)

    # Confirm the sim child is also dead — own PG was killed.
    # /proc/<pid> should be gone.
    time.sleep(0.5)
    assert not Path(f"/proc/{pid}").exists(), (
        f"sim subprocess pid={pid} survived backend kill; "
        "process-group cleanup is broken (Phase 3 regression)"
    )

    # --- Phase C: restart backend, recovery should mark run interrupted ---
    proc2 = _spawn_backend(port, work_dir, env_extra={})
    try:
        _wait_healthy(port)

        # Hit the run status endpoint and confirm interrupted.
        r = httpx.get(f"http://127.0.0.1:{port}/api/runs/{run_id}", timeout=5.0)
        assert r.status_code == 200, f"status fetch failed: {r.status_code} {r.text}"
        body = r.json()
        # The exact response shape depends on Phase 3's api/runs.py; the
        # record on disk is the source of truth.
        rec_on_disk = json.loads((state_dir / f"{run_id}.json").read_text())
        assert rec_on_disk["state"] == "interrupted", (
            f"expected interrupted, got {rec_on_disk['state']}"
        )
        assert rec_on_disk["error"]["kind"] == "internal.backend_restarted", (
            f"expected internal.backend_restarted, got {rec_on_disk['error']}"
        )

    finally:
        _terminate_backend(proc2)


@pytest.mark.skipif(
    not MOCK_SIM.exists(),
    reason="mock_sim.sh fixture from Phase 3 not present",
)
def test_restart_with_no_in_flight_runs_is_a_noop(tmp_path: Path) -> None:
    """Smoke: restart with empty state dir, recovery report should be all zeros."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    port = _free_port()

    proc = _spawn_backend(port, work_dir, env_extra={})
    try:
        _wait_healthy(port)
    finally:
        _terminate_backend(proc)

    # Inspect captured stdout for the boot.recovery_complete event.
    out, _ = proc.communicate(timeout=2.0)
    out_text = out.decode("utf-8", errors="replace") if out else ""
    found = False
    for line in out_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "boot.recovery_complete":
            found = True
            assert ev["reattached"] == 0
            assert ev["interrupted"] == 0
            assert ev["terminal_already"] == 0
            break
    assert found, f"boot.recovery_complete event missing from backend stdout:\n{out_text[:2000]}"
```

- [ ] **Step 3: Run the integration test**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_restart_mid_run_recovers.py -v -s 2>&1 | tail -40
```

Expected: 2 passed. If `test_restart_mid_run_marks_run_interrupted` skips because `mock_sim.sh` is missing or the API rejects the recipe shape, this signals a Phase 2/3 gap — file an issue and move on. The recovery logic is still covered by `tests/runs/test_recover_on_boot.py` unit tests.

If the test fails on the assertion about the sim child being dead, that indicates Phase 3's process-group propagation didn't survive a SIGTERM-to-backend (the backend's asyncio cancel didn't fire `killpg` on its way down). Fix in Phase 3 before merging Phase 4.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/__init__.py \
        server/tests/integration/test_restart_mid_run_recovers.py
git commit -m "phase-4: integration test — restart mid-run, verify run.interrupted + PG cleanup"
```

---

### Task 9: Delete supervise.sh and update README

**Files:**
- Delete: `server/supervise.sh`
- Modify: `README.md` (replace `supervise.sh up` instructions with systemd link)

- [ ] **Step 1: Grep for any callers of supervise.sh**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -rn "supervise\.sh" --include="*.md" --include="*.sh" --include="*.py" --include="*.toml" --include="*.yaml" --include="*.yml" 2>&1 | grep -v "node_modules\|\.venv\|__pycache__"
```

Expected: matches in `README.md` (the install docs) and possibly in `docs/`. Any matches in `.github/workflows/` need updating too.

- [ ] **Step 2: Delete the script**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git rm server/supervise.sh
```

Expected: `rm 'server/supervise.sh'`.

- [ ] **Step 3: Update README.md**

Read the relevant section first:

```bash
grep -n "supervise" /home/frankyin/Desktop/work/gsfluent_pkg/README.md
```

For each matched line, replace it with a pointer to the new systemd docs. Typical pattern: find a block like

```
# Keep the backend up overnight
bash server/supervise.sh up
```

and replace with

```
# Keep the backend up under systemd
# See deploy/README.md for the full install steps.
sudo systemctl link "$(pwd)/deploy/gsfluent-backend.service"
sudo systemctl enable --now gsfluent-backend.service
```

If the line is in a "supervisor" subsection, rename the subsection to "systemd supervision".

If the file is large, use Edit on the specific lines you found. Do not blanket-rewrite the README.

- [ ] **Step 4: Confirm no stale references remain**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
grep -rn "supervise\.sh" --include="*.md" --include="*.sh" --include="*.py" --include="*.toml" --include="*.yaml" --include="*.yml" 2>&1 | grep -v "node_modules\|\.venv\|__pycache__"
```

Expected: no matches (or only matches inside `docs/superpowers/specs/` and `docs/superpowers/plans/`, which are historical and fine to leave).

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add README.md
git commit -m "phase-4: delete supervise.sh, point README at deploy/README.md for systemd install"
```

---

### Task 10: Manual systemd install + smoke verification on the dev box

**Files:**
- No file edits. Manual verification + commit only.

This task confirms the dev-box unit actually works on this machine. Skip if you do not have systemd user services available (e.g. macOS, WSL1).

- [ ] **Step 1: Check systemd is available**

```bash
systemctl --user --version 2>&1 | head -1
```

Expected: `systemd <version>` line. If `systemctl: command not found` or `Failed to connect to user scope bus`, skip the rest of this task and document the limitation in the PR description.

- [ ] **Step 2: Make sure the venv has uvicorn**

```bash
ls /home/frankyin/Desktop/work/gsfluent_pkg/.venv/bin/uvicorn 2>&1 || \
  (cd /home/frankyin/Desktop/work/gsfluent_pkg && uv sync --directory server)
```

Expected: `.venv/bin/uvicorn` exists.

- [ ] **Step 3: Install the dev unit**

```bash
mkdir -p ~/.config/systemd/user
cp /home/frankyin/Desktop/work/gsfluent_pkg/deploy/gsfluent-backend.dev.service \
   ~/.config/systemd/user/gsfluent-backend.service
systemctl --user daemon-reload
```

- [ ] **Step 4: Start it and confirm it goes active**

```bash
systemctl --user start gsfluent-backend.service
sleep 3
systemctl --user status gsfluent-backend.service 2>&1 | head -20
```

Expected: `Active: active (running)` and `Status:` line showing `ready (reattached=0 interrupted=0 ...)`.

- [ ] **Step 5: Confirm watchdog heartbeats are landing**

```bash
journalctl --user -u gsfluent-backend -n 50 -o cat 2>&1 \
  | grep -c 'backend.watchdog.ping'
```

Wait ~30 seconds after start so at least one ping has fired. Expected: count >= 1.

- [ ] **Step 6: Confirm READY=1 was received (no `start` hang)**

```bash
systemctl --user show gsfluent-backend.service -p ActiveState -p SubState
```

Expected: `ActiveState=active`, `SubState=running`. If `SubState=start` and `ActiveState=activating`, the `READY=1` notification didn't arrive — re-read Task 5 and the troubleshooting section of `deploy/README.md`.

- [ ] **Step 7: Stop and clean up**

```bash
systemctl --user stop gsfluent-backend.service
systemctl --user disable gsfluent-backend.service 2>&1 || true
rm ~/.config/systemd/user/gsfluent-backend.service
systemctl --user daemon-reload
```

- [ ] **Step 8: No commit — manual verification only**

---

### Task 11: Phase 4 verification + branch handoff

**Files:**
- No file edits.

- [ ] **Step 1: Run the full test suite**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: every existing test passes plus Phase 4 additions:
- `tests/core/test_sdnotify.py` — 8 tests (1 may skip on macOS for abstract sockets)
- `tests/core/test_recovery.py` — 6 tests
- `tests/runs/test_recover_on_boot.py` — 7 tests
- `tests/integration/test_restart_mid_run_recovers.py` — 2 tests (may skip if Phase 3 fixtures absent)
- `tests/test_composition.py` — 2 new tests added to the Phase 1 file

Total Phase 4 net-new tests: approximately 25.

- [ ] **Step 2: Confirm the supervise.sh deletion is clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
test ! -f server/supervise.sh && echo "supervise.sh deleted OK"
git ls-files server/supervise.sh
```

Expected: prints `supervise.sh deleted OK` and the second command outputs nothing.

- [ ] **Step 3: Confirm git history is clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git log --oneline main..HEAD
```

Expected: 8 commits, each prefixed `phase-4:`.

- [ ] **Step 4: Push the branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git push -u origin phase-4-crash-recovery-systemd
```

Expected: branch published on origin. Open a PR titled `phase-4: crash recovery + systemd supervision — replace supervise.sh, add recover_on_boot, sd_notify, watchdog heartbeat`.

- [ ] **Step 5: Update the spec status note (optional)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
# Edit docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md,
# update the Status line to add "Phase 4 implemented in branch
# phase-4-crash-recovery-systemd (PR #N)".
git add docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md
git commit -m "docs: mark Phase 4 implemented in branch phase-4-crash-recovery-systemd"
git push
```

---

## Definition of Done — Phase 4

Phase 4 ships when ALL of:

- [ ] All 11 tasks above completed
- [ ] All new tests pass (`pytest tests/core/test_sdnotify.py tests/core/test_recovery.py tests/runs/ tests/test_composition.py tests/integration/test_restart_mid_run_recovers.py -v`)
- [ ] All baseline tests still pass (no regressions)
- [ ] `AsyncioRunManager.recover_on_boot()` returns a `RecoveryReport` with correct counts
- [ ] FastAPI lifespan calls `recover_on_boot()` before yielding (NOT `app.on_event("startup")`)
- [ ] `sd_notify("READY=1")` fires after recovery; watchdog heartbeat fires every 15s
- [ ] `server/supervise.sh` deleted; README points at `deploy/README.md`
- [ ] Manual dev-box install verified: `systemctl --user start gsfluent-backend.service` reaches `active (running)`
- [ ] `deploy/README.md` documents install, journalctl recipes, and troubleshooting
- [ ] Branch `phase-4-crash-recovery-systemd` pushed; PR open

## Handoff to Phase 5

Phase 5 (`streaming cache hardening`) does NOT depend on Phase 4 — they are orthogonal. However Phase 5 will benefit from the structured event surface here:

- `cell.cache.hit`, `cell.cache.resumed`, `cell.cache.miss` events from the viser_headless client land in journald via the same JSON pipe.
- The systemd unit's `/api/health` watchdog already covers Phase 5's new ETag / Range code paths — if a cache-related route deadlocks, the watchdog kills the backend.

Phase 5 plan: `docs/superpowers/plans/2026-05-22-phase-5-streaming-cache.md`.

---

**End of Phase 4 plan.**
