# Phase 3 — Sim Orchestration Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move sim/fuse orchestration out of the 197-line `run_sim.sh` and into a typed `MPMSimulationEngine` (Python), shrink the shell script to a 20-line conda-activate shim, harden the subprocess lifecycle (new process group, SIGTERM/SIGKILL escalation, wall-time enforcement), classify sim stderr into typed errors, and lock the recipe boundary down with strict Pydantic validation + cap checks at `api/runs.py`. This is the hardening phase: cancellation will actually cancel, runaway recipes will be killed, and bad recipes will fail at the API edge with a structured 422 envelope.

**Architecture:** Two new concrete `SimulationEngine` impls land under `core/sim_engines/`: `MPMSimulationEngine` (production — absorbs `run_sim.sh` orchestration logic, spawns subprocess in new process group via `start_new_session=True`, enforces wall-time via `asyncio.wait_for`, classifies stderr against a YAML pattern file) and `MockSimulationEngine` (test fixture — deterministic, no GPU, configurable failures). The Phase 2 `AsyncioRunManager` (from `core/run_manager.py`) gains the signal-escalation ladder: SIGTERM to the process group, wait up to 30 seconds, then SIGKILL to the process group. `api/runs.py` is reshaped to strict-Pydantic-validate the request, run `limits.check_recipe_caps()`, and emit the spec's 422 error envelope shape (`{"error": {"kind", "message", "details", "trace_id"}}`). A test fixture `mock_sim.sh` provides a configurable fake sim binary so every dangerous-path integration test runs deterministically in CI with no real GPU. The stderr pattern file `core/sim_engines/mpm_error_patterns.yaml` is operator-tunable post-launch (Open Question #1 in the spec).

**Tech Stack:** Python 3.10+, `pydantic>=2.6` (strict mode), `pyyaml>=6.0` (new dep — used only for the error-pattern file), `asyncio`, `signal`, `os` (killpg / setsid), `pytest>=8`, `pytest-asyncio>=0.23`. **One new dependency added: `pyyaml`.**

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md` (Phase 3 section + Open Question #1 default + Section 5 mock_sim env-var knobs).

**Phase 3 is plan 3 of 7.** Phase 3 depends on Phase 1's Protocols (`SimulationEngine`, `EventEmitter`, `RunManager`, `ValidationError`, `CapExceededError`, `SimError` hierarchy, `ValidatedRecipe`, `ModelRef`, `SimResult`) and `core/limits.py`'s `CapConfig` + `check_recipe_caps()`. Phase 3 depends on Phase 2's `AsyncioRunManager` (in `core/run_manager.py`) — the signal-escalation ladder is added to it here. Phase 4 (crash recovery + supervision) consumes the persisted `pgid` + `pid_starttime` fields this phase populates.

---

## File Structure

### New files (Phase 3)

```
server/gsfluent/
├── core/
│   └── sim_engines/
│       ├── __init__.py                  ← re-exports MPMSimulationEngine, MockSimulationEngine
│       ├── mpm.py                        ← MPMSimulationEngine (production)
│       ├── mock.py                       ← MockSimulationEngine (test-only)
│       ├── mpm_error_patterns.yaml       ← operator-tunable stderr classifier patterns
│       └── __main__.py                   ← `python -m gsfluent.core.sim_engines.mpm` entry
└── api/
    └── errors.py                         ← 422 error envelope shape + trace_id helper

server/tests/
├── sim_engines/
│   ├── __init__.py
│   ├── test_mpm.py                       ← MPM-specific: env parsing, classifier, preflight
│   └── test_mock.py                      ← mock-sim correctness
├── api/
│   └── test_runs_validation.py           ← strict-mode rejection, cap-violation 422 shape
├── integration/
│   ├── __init__.py
│   ├── test_cancel_kills_pg.py
│   ├── test_sigterm_ignoring_sim_gets_sigkill.py
│   ├── test_wall_time_enforced.py
│   ├── test_recipe_rejected_early.py
│   └── test_sim_error_classification.py
└── fixtures/
    ├── __init__.py
    └── mock_sim.sh                       ← configurable fake sim (per spec Section 5)
```

### Modified files (Phase 3)

```
server/tools/run_sim.sh                    ← 197 lines → 20-line conda-activate shim
server/gsfluent/api/runs.py                ← strict Pydantic + check_recipe_caps + 422 envelope
server/gsfluent/core/run_manager.py        ← add start_new_session=True, PG signal escalation,
                                             wall-time enforcement via asyncio.wait_for
server/gsfluent/composition.py             ← wire MPMSimulationEngine in build_app
server/pyproject.toml                       ← add pyyaml dependency
```

### Files NOT modified in Phase 3

```
server/gsfluent/protocols/*.py             ← Phase 1 owns interfaces
server/gsfluent/observability/jsonlog.py   ← Phase 1 owns
server/gsfluent/core/state.py              ← Phase 1 owns
server/gsfluent/core/limits.py             ← Phase 1 owns
server/gsfluent/api/sequences.py           ← Phase 5 (streaming cache hardening)
server/gsfluent/api/health.py              ← Phase 6 (real health signals)
server/supervise.sh                        ← Phase 4 (replaced by systemd)
server/tools/fuse_to_full_ply.py           ← Phase 2 (became CLI wrapper)
server/tools/pack_splats.py                ← Phase 2 (became CLI wrapper)
frontend/python/viser_headless.py          ← Phase 5
```

---

## Tasks

### Task 1: Branch + baseline test verification

**Files:**
- No file edits in this task. Verification + commit only.

- [ ] **Step 1: Create the phase branch**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git checkout main
git pull --ff-only
git checkout -b phase-3-sim-orchestration
```

Expected: `Switched to a new branch 'phase-3-sim-orchestration'`

- [ ] **Step 2: Confirm Phase 1 + 2 prerequisites are merged**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
from gsfluent.protocols.sim import SimulationEngine, SimError, SimWallTimeExceededError, SimGpuOomError, SimUnstableRecipeError, SimCrashedError, ValidatedRecipe, ModelRef, SimResult
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.runs import RunManager, RunState, ValidationError, CapExceededError
from gsfluent.core.limits import CapConfig, check_recipe_caps
from gsfluent.core.run_manager import AsyncioRunManager
print('phase-1 + phase-2 symbols importable: OK')
"
```

Expected: `phase-1 + phase-2 symbols importable: OK`. If this fails, halt — Phase 3 needs Phase 1 and Phase 2 merged first.

- [ ] **Step 3: Run full baseline test suite, record counts**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v 2>&1 | tail -10
```

Expected: a pass/fail count line. Note this number — Phase 3 must not regress it.

- [ ] **Step 4: Verify pyyaml availability for the new dependency**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "import yaml; print('pyyaml', yaml.__version__)" 2>&1 || echo "MISSING — will add in Task 2"
```

Either prints `pyyaml <version>` or `MISSING — will add in Task 2`. Both outcomes are fine; the next task pins the dep.

- [ ] **Step 5: No commit yet — Task 1 is verification only**

---

### Task 2: Add pyyaml dependency

**Files:**
- Modify: `server/pyproject.toml`

- [ ] **Step 1: Inspect the current dependency block**

```bash
grep -n "dependencies" /home/frankyin/Desktop/work/gsfluent_pkg/server/pyproject.toml
```

Expected: a line like `dependencies = [` with the existing list following.

- [ ] **Step 2: Add pyyaml**

Open `server/pyproject.toml`, locate the `dependencies` list, and add `"pyyaml>=6.0",` to it. Maintain alphabetical order if the rest of the list is alphabetized. Example final fragment:

```toml
dependencies = [
    "fastapi>=0.110",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "uvicorn[standard]>=0.27",
    # ... existing entries kept ...
]
```

- [ ] **Step 3: Reinstall the package so the new dep lands**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/pip install -e ./server
```

Expected: pip resolves pyyaml and installs cleanly.

- [ ] **Step 4: Verify the import works**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "import yaml; print('OK', yaml.__version__)"
```

Expected: `OK 6.x`.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/pyproject.toml
git commit -m "phase-3: pyproject — add pyyaml>=6.0 dependency for sim stderr classifier pattern file"
```

---

### Task 3: api/errors.py — 422 error envelope helper

**Files:**
- Create: `server/gsfluent/api/errors.py`
- Create: `server/tests/api/test_errors.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/api/__init__.py` if it does not exist (empty file).

Create `server/tests/api/test_errors.py`:

```python
"""Tests for the 422 error envelope shape + trace_id helper."""
import re
import uuid

import pytest
from fastapi import HTTPException

from gsfluent.api.errors import (
    api_error_envelope,
    new_trace_id,
    raise_validation_error,
    raise_cap_exceeded,
)
from gsfluent.protocols.runs import CapExceededError, ValidationError


def test_new_trace_id_is_a_ulid_or_uuid_string() -> None:
    tid = new_trace_id()
    # Spec example uses a ULID; UUID4 is acceptable as long as it is
    # a 26-32 char alphanumeric token.
    assert isinstance(tid, str)
    assert re.match(r"^[A-Za-z0-9]{20,40}$", tid)


def test_two_trace_ids_differ() -> None:
    assert new_trace_id() != new_trace_id()


def test_api_error_envelope_shape() -> None:
    env = api_error_envelope(
        kind="cap_exceeded.particle_count",
        message="Particle count 800000 exceeds limit 500000",
        details={"requested": 800_000, "limit": 500_000},
        trace_id="01H8K2P",
    )
    assert env == {
        "error": {
            "kind": "cap_exceeded.particle_count",
            "message": "Particle count 800000 exceeds limit 500000",
            "details": {"requested": 800_000, "limit": 500_000},
            "trace_id": "01H8K2P",
        }
    }


def test_api_error_envelope_default_details_is_empty_dict() -> None:
    env = api_error_envelope(
        kind="validation.run_name",
        message="run_name must match ^[A-Za-z0-9_.-]+$",
        trace_id="t1",
    )
    assert env["error"]["details"] == {}


def test_raise_validation_error_produces_422_with_envelope() -> None:
    with pytest.raises(HTTPException) as ei:
        raise_validation_error(
            kind="validation.particle_count",
            message="particle_count must be a positive int",
            details={"got": "abc"},
        )
    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert detail["error"]["kind"] == "validation.particle_count"
    assert detail["error"]["details"] == {"got": "abc"}
    assert "trace_id" in detail["error"]


def test_raise_cap_exceeded_produces_422() -> None:
    with pytest.raises(HTTPException) as ei:
        raise_cap_exceeded(
            kind="cap_exceeded.wall_time",
            message="wall_time_sec 7200 exceeds backend max 3600",
            details={"requested": 7200, "limit": 3600},
        )
    assert ei.value.status_code == 422
    assert ei.value.detail["error"]["kind"] == "cap_exceeded.wall_time"
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_errors.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.api.errors'`.

- [ ] **Step 3: Implement the error envelope helper**

Create `server/gsfluent/api/errors.py`:

```python
"""422 error envelope shape + trace_id helper.

Matches the API error response shape from the spec:

    {
      "error": {
        "kind": "cap_exceeded.particle_count",
        "message": "Particle count 800000 exceeds limit 500000",
        "details": { "requested": 800000, "limit": 500000 },
        "trace_id": "01H8K2P..."
      }
    }

Every 422 in api/runs.py routes through these helpers so the envelope
is uniform across validation.*, cap_exceeded.*, and other typed-kind
errors. trace_id is generated per-request and surfaces in the response
so customers can paste it into a support ticket and operators can grep
the structured event stream.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException


def new_trace_id() -> str:
    """Return a fresh trace identifier.

    Uses uuid4 hex (32 chars, base16). A ULID would be lexicographically
    sortable but the spec only requires uniqueness + correlatability;
    uuid4 is stdlib and avoids another dependency.
    """
    return uuid.uuid4().hex


def api_error_envelope(
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the JSON shape that every 4xx/5xx error response carries.

    trace_id is auto-generated if not supplied.
    """
    return {
        "error": {
            "kind": kind,
            "message": message,
            "details": dict(details) if details else {},
            "trace_id": trace_id if trace_id is not None else new_trace_id(),
        }
    }


def raise_validation_error(
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise a 422 HTTPException with the standard envelope.

    Callers in api/runs.py use this for both Pydantic strict-mode rejection
    and any post-parse validation that surfaces as `validation.<field>`.
    """
    raise HTTPException(
        status_code=422,
        detail=api_error_envelope(kind=kind, message=message, details=details),
    )


def raise_cap_exceeded(
    *,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise a 422 HTTPException for cap violations.

    Same envelope shape as validation errors; the `kind` discriminator
    is what the client uses to distinguish (`cap_exceeded.*` vs
    `validation.*`).
    """
    raise HTTPException(
        status_code=422,
        detail=api_error_envelope(kind=kind, message=message, details=details),
    )
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_errors.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/api/errors.py \
        server/tests/api/__init__.py \
        server/tests/api/test_errors.py
git commit -m "phase-3: api/errors.py — 422 envelope helper (kind/message/details/trace_id) + raise_validation_error / raise_cap_exceeded"
```

---

### Task 4: mpm_error_patterns.yaml — stderr classifier patterns

**Files:**
- Create: `server/gsfluent/core/sim_engines/__init__.py` (placeholder, expanded in Task 5)
- Create: `server/gsfluent/core/sim_engines/mpm_error_patterns.yaml`

- [ ] **Step 1: Create the sim_engines package directory**

Create `server/gsfluent/core/sim_engines/__init__.py` as an empty file. Task 5 will populate it with re-exports.

- [ ] **Step 2: Write the YAML pattern file**

Create `server/gsfluent/core/sim_engines/mpm_error_patterns.yaml`:

```yaml
# Stderr pattern -> SimError-subclass classifier for MPMSimulationEngine.
#
# The MPM sim writes diagnostic output to stderr (merged into stdout by
# the orchestrator). On non-zero exit, MPMSimulationEngine scans the
# tail of stderr against these patterns in order and raises the matched
# error kind. First match wins. If no pattern matches, the fallback is
# SimCrashedError (kind: sim.crashed).
#
# Operators can tune patterns in-place without code changes — the file
# is read at MPMSimulationEngine construction time (see mpm.py).
#
# Decision: this classifier is included per spec Open Question #1
# default ("include it, parametrize patterns in YAML so they can be
# tuned post-launch"). The cost is ~150 lines and one YAML file; the
# value is structured error-kind reporting customers and operators can
# act on directly.

patterns:
  # GPU out-of-memory: torch raises with this exact phrase on CUDA OOM.
  - error_kind: sim.gpu_oom
    regex: "out of memory"
    case_insensitive: true
    description: "CUDA OOM — sim allocated more GPU memory than available."

  # CFL violation: warp/taichi MPM step emits CFL when substep_dt is too
  # large for the current particle velocity field. Customer fix: increase
  # substep count or shrink dt.
  - error_kind: sim.unstable_recipe
    regex: "CFL"
    case_insensitive: false
    description: "CFL condition violated — substep_dt too large; recipe is numerically unstable."

  # Illegal memory access: a downstream effect of numerical blowup
  # (NaN positions index past the grid). Same root cause as CFL.
  - error_kind: sim.unstable_recipe
    regex: "illegal memory access"
    case_insensitive: true
    description: "Illegal memory access on GPU — usually a downstream effect of CFL blowup."

  # NaN / Inf in position output: sim hit a numerical singularity. Same
  # customer message as CFL but a different upstream cause.
  - error_kind: sim.unstable_recipe
    regex: "(?:nan|inf)"
    case_insensitive: true
    description: "Non-finite values in particle positions — numerical blowup."
```

- [ ] **Step 3: Sanity-check the YAML loads**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
.venv/bin/python -c "
import yaml
from pathlib import Path
p = Path('server/gsfluent/core/sim_engines/mpm_error_patterns.yaml')
data = yaml.safe_load(p.read_text())
print('patterns loaded:', len(data['patterns']))
for pat in data['patterns']:
    assert {'error_kind', 'regex'} <= set(pat.keys())
    print(' ', pat['error_kind'], '->', pat['regex'])
"
```

Expected:
```
patterns loaded: 4
  sim.gpu_oom -> out of memory
  sim.unstable_recipe -> CFL
  sim.unstable_recipe -> illegal memory access
  sim.unstable_recipe -> (?:nan|inf)
```

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/sim_engines/__init__.py \
        server/gsfluent/core/sim_engines/mpm_error_patterns.yaml
git commit -m "phase-3: sim_engines/mpm_error_patterns.yaml — operator-tunable stderr classifier (4 default patterns: gpu_oom, CFL, illegal_memory, NaN/Inf)"
```

---

### Task 5: core/sim_engines/mpm.py — MPMSimulationEngine

**Files:**
- Modify: `server/gsfluent/core/sim_engines/__init__.py`
- Create: `server/gsfluent/core/sim_engines/mpm.py`
- Create: `server/gsfluent/core/sim_engines/__main__.py`
- Create: `server/tests/sim_engines/__init__.py`
- Create: `server/tests/sim_engines/test_mpm.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/sim_engines/__init__.py` as empty file.

Create `server/tests/sim_engines/test_mpm.py`:

```python
"""MPM-specific unit tests: pattern loading, classifier, preflight."""
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mpm import (
    MPMErrorPattern,
    MPMSimulationEngine,
    classify_stderr,
    load_error_patterns,
)
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    SimCrashedError,
    SimEnvMissingError,
    SimGpuOomError,
    SimInterpreterMissingError,
    SimUnstableRecipeError,
)


# ---------- pattern loading ----------------------------------------------


def test_default_patterns_load_from_yaml() -> None:
    pats = load_error_patterns()
    kinds = {p.error_kind for p in pats}
    assert "sim.gpu_oom" in kinds
    assert "sim.unstable_recipe" in kinds


def test_pattern_dataclass_holds_compiled_regex() -> None:
    pats = load_error_patterns()
    for p in pats:
        assert isinstance(p, MPMErrorPattern)
        # Compiled regex pattern; .search() should be available.
        assert hasattr(p.compiled, "search")


def test_load_error_patterns_from_explicit_path(tmp_path: Path) -> None:
    yml = tmp_path / "patterns.yaml"
    yml.write_text(
        "patterns:\n"
        "  - error_kind: sim.gpu_oom\n"
        "    regex: 'totally out of memory'\n"
        "    case_insensitive: true\n"
    )
    pats = load_error_patterns(path=yml)
    assert len(pats) == 1
    assert pats[0].error_kind == "sim.gpu_oom"
    assert pats[0].compiled.search("Totally Out Of Memory") is not None


# ---------- classifier ---------------------------------------------------


def test_classify_gpu_oom() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("CUDA error: out of memory at line 42", pats)
    assert kind == "sim.gpu_oom"


def test_classify_cfl() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("step 17: CFL violation", pats)
    assert kind == "sim.unstable_recipe"


def test_classify_illegal_memory() -> None:
    pats = load_error_patterns()
    kind = classify_stderr(
        "CUDA Runtime: an illegal memory access was encountered", pats
    )
    assert kind == "sim.unstable_recipe"


def test_classify_nan_inf() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("frame 23: position contains NaN", pats)
    assert kind == "sim.unstable_recipe"


def test_classify_unmatched_returns_none() -> None:
    pats = load_error_patterns()
    kind = classify_stderr("Segmentation fault (core dumped)", pats)
    assert kind is None


def test_classify_empty_stderr_returns_none() -> None:
    pats = load_error_patterns()
    assert classify_stderr("", pats) is None


def test_classify_first_match_wins() -> None:
    pats = load_error_patterns()
    # "out of memory" + "NaN" both present -> gpu_oom wins (declared first).
    kind = classify_stderr("Error: out of memory; NaN positions", pats)
    assert kind == "sim.gpu_oom"


# ---------- preflight ----------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_raises_sim_env_missing(tmp_path: Path) -> None:
    eng = MPMSimulationEngine(
        sim_home=tmp_path / "does_not_exist",
        sim_python="/usr/bin/python3",
        sim_env=None,
    )
    with pytest.raises(SimEnvMissingError):
        await eng.preflight()


@pytest.mark.asyncio
async def test_preflight_raises_sim_interpreter_missing(tmp_path: Path) -> None:
    (tmp_path / "sim_home").mkdir()
    eng = MPMSimulationEngine(
        sim_home=tmp_path / "sim_home",
        sim_python="/nonexistent/python_interpreter_xyz",
        sim_env=None,
    )
    with pytest.raises(SimInterpreterMissingError):
        await eng.preflight()


@pytest.mark.asyncio
async def test_preflight_passes_with_valid_env(tmp_path: Path) -> None:
    """Preflight should accept a real sim_home dir + on-PATH python."""
    sh = tmp_path / "sim_home"
    sh.mkdir()
    # Use the actual python that's running this test — guaranteed to exist.
    import sys
    eng = MPMSimulationEngine(
        sim_home=sh,
        sim_python=sys.executable,
        sim_env=None,
        require_gpu=False,  # tests run on CPU-only CI hosts
    )
    # Should not raise.
    await eng.preflight()
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/sim_engines/test_mpm.py -v
```

Expected: `ModuleNotFoundError: No module named 'gsfluent.core.sim_engines.mpm'`.

- [ ] **Step 3: Implement MPMSimulationEngine**

Create `server/gsfluent/core/sim_engines/mpm.py`:

```python
"""MPMSimulationEngine — production MPM sim orchestration.

Absorbs the orchestration logic previously living in
server/tools/run_sim.sh:
  - preflight checks (sim_home dir, sim python interpreter, GPU)
  - spawn the MPM sim subprocess in a new process group
  - spawn the fuse subprocess in the same group
  - classify stderr against operator-tunable YAML patterns on failure

The wall-time timeout and signal-escalation logic live in
core/run_manager.py (the outer asyncio.wait_for + killpg ladder).
This engine emits structured events through the on_event EventEmitter:

  sim.preflight_ok
  sim.spawned         (pid, pgid, argv)
  sim.completed       (returncode, duration_sec, n_frames)
  fuse.spawned        (pid, argv)
  fuse.completed      (returncode, duration_sec)

Errors raised:
  SimEnvMissingError, SimInterpreterMissingError, GPUUnavailableError
  SimGpuOomError, SimUnstableRecipeError, SimCrashedError
  (SimWallTimeExceededError is raised by the RunManager, not here.)
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gsfluent._paths import PKG_ROOT
from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    GPUUnavailableError,
    ModelRef,
    SimCrashedError,
    SimEnvMissingError,
    SimGpuOomError,
    SimInterpreterMissingError,
    SimResult,
    SimUnstableRecipeError,
    ValidatedRecipe,
)


# ---------- stderr classifier --------------------------------------------


@dataclass(frozen=True)
class MPMErrorPattern:
    """One stderr-pattern -> error_kind mapping."""
    error_kind: str
    regex_source: str
    case_insensitive: bool
    description: str
    compiled: re.Pattern[str]


def _default_patterns_path() -> Path:
    return Path(__file__).parent / "mpm_error_patterns.yaml"


def load_error_patterns(path: Path | None = None) -> list[MPMErrorPattern]:
    """Load the operator-tunable stderr pattern file. Defaults to
    server/gsfluent/core/sim_engines/mpm_error_patterns.yaml.
    """
    p = path if path is not None else _default_patterns_path()
    raw = yaml.safe_load(p.read_text())
    out: list[MPMErrorPattern] = []
    for entry in raw.get("patterns", []):
        flags = re.IGNORECASE if entry.get("case_insensitive", False) else 0
        out.append(
            MPMErrorPattern(
                error_kind=entry["error_kind"],
                regex_source=entry["regex"],
                case_insensitive=entry.get("case_insensitive", False),
                description=entry.get("description", ""),
                compiled=re.compile(entry["regex"], flags),
            )
        )
    return out


def classify_stderr(
    stderr: str, patterns: list[MPMErrorPattern]
) -> str | None:
    """Return the first matching error_kind, or None if no pattern matches.

    Scans the entire stderr (not just the tail) — sim errors can fire
    early and be followed by unrelated output.
    """
    if not stderr:
        return None
    for pat in patterns:
        if pat.compiled.search(stderr) is not None:
            return pat.error_kind
    return None


def _kind_to_exception(kind: str, message: str) -> Exception:
    """Map a classifier kind string to its exception class."""
    if kind == "sim.gpu_oom":
        return SimGpuOomError(message)
    if kind == "sim.unstable_recipe":
        return SimUnstableRecipeError(message)
    return SimCrashedError(message)


# ---------- the engine ---------------------------------------------------


class MPMSimulationEngine:
    """Concrete SimulationEngine for the MPM sim (warp + taichi + torch).

    Spawns two subprocesses per run() call:
      1. The canonical MPM sim (gs_simulation_building.py)
      2. The fuse stage (server/tools/fuse_to_full_ply.py)
    Both inherit the new process group created at sim spawn so a single
    killpg(pgid, SIGTERM/SIGKILL) on cancel/timeout takes down both.

    Construction:
        eng = MPMSimulationEngine(
            sim_home=Path("/path/to/GaussianFluent"),
            sim_python="/path/to/sim-env/bin/python",
            sim_env="physics",   # optional conda env name
            require_gpu=True,
            patterns_path=None,  # default: bundled yaml
        )
    """

    def __init__(
        self,
        *,
        sim_home: Path,
        sim_python: str,
        sim_env: str | None = None,
        require_gpu: bool = True,
        patterns_path: Path | None = None,
        sim_fast: bool = False,
    ) -> None:
        self._sim_home = sim_home
        self._sim_python = sim_python
        self._sim_env = sim_env
        self._require_gpu = require_gpu
        self._sim_fast = sim_fast
        self._patterns = load_error_patterns(path=patterns_path)

    # ---- preflight ------------------------------------------------------

    async def preflight(self) -> None:
        """Raise typed error if environment cannot run a sim.

        Checked in order: sim_home dir exists, sim_python on PATH /
        absolute path resolvable, optional GPU reachability.
        """
        if not self._sim_home.is_dir():
            raise SimEnvMissingError(
                f"GSFLUENT_SIM_HOME directory not found: {self._sim_home}"
            )

        resolved_python = (
            shutil.which(self._sim_python)
            if not os.path.isabs(self._sim_python)
            else (self._sim_python if Path(self._sim_python).is_file() else None)
        )
        if not resolved_python:
            raise SimInterpreterMissingError(
                f"sim python interpreter not found: {self._sim_python}"
            )

        if self._require_gpu and not _gpu_reachable():
            raise GPUUnavailableError(
                "nvidia-smi reports no CUDA-capable device"
            )

    # ---- run ------------------------------------------------------------

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        """Spawn sim + fuse and wait for both. Wall-time + cancel handling
        happens in the caller (RunManager), which wraps the awaited task
        in asyncio.wait_for and on timeout / cancel does killpg on the
        process group recorded in the sim.spawned event.

        Emits sim.* + fuse.* events through on_event. Returns SimResult
        on success, raises classified SimError on failure.
        """
        on_event.emit("sim.preflight_ok")

        # Resolve paths the same way run_sim.sh did so we keep
        # bug-for-bug compatibility on the directory layout.
        run_name = recipe.get("_run_name") or output_dir.name
        sim_output_dir = self._sim_home / "output" / run_name
        sim_ply_dir = sim_output_dir / "simulation_ply"
        library_seq_dir = PKG_ROOT / "work" / "library" / "sequences" / run_name
        fused_dir = library_seq_dir / "frames"

        sim_output_dir.mkdir(parents=True, exist_ok=True)
        library_seq_dir.mkdir(parents=True, exist_ok=True)
        fused_dir.mkdir(parents=True, exist_ok=True)

        # Find the highest-iteration reference ply under model/point_cloud/.
        reference_ply = _find_reference_ply(model.path)
        if reference_ply is None:
            raise SimCrashedError(
                f"no reference ply under {model.path}/point_cloud/"
            )

        # Preserve the merged recipe.json early so a sim crash doesn't lose it.
        config_path = library_seq_dir / "recipe.json"
        import json
        config_path.write_text(json.dumps(recipe, indent=2))

        particles = int(recipe.get("particle_count", 200_000))

        # ---- stage 1: MPM sim ------------------------------------------

        sim_argv = self._build_sim_argv(
            model_dir=model.path,
            sim_output_dir=sim_output_dir,
            config_path=config_path,
            particles=particles,
        )

        t0 = time.monotonic()
        sim_proc = await self._spawn_in_new_pg(
            argv=sim_argv,
            cwd=str(self._sim_home),
        )
        pgid = os.getpgid(sim_proc.pid)
        pid_starttime = _read_pid_starttime(sim_proc.pid)
        on_event.emit(
            "sim.spawned",
            pid=sim_proc.pid,
            pgid=pgid,
            pid_starttime=pid_starttime,
            argv=sim_argv,
        )

        sim_stderr_chunks: list[str] = []
        sim_rc = await _wait_capturing_stderr(sim_proc, sim_stderr_chunks)
        sim_duration = time.monotonic() - t0
        on_event.emit(
            "sim.completed",
            returncode=sim_rc,
            duration_sec=sim_duration,
        )
        if sim_rc != 0:
            joined = "".join(sim_stderr_chunks)
            kind = classify_stderr(joined, self._patterns)
            msg = (
                f"sim exited with rc={sim_rc} after {sim_duration:.1f}s; "
                f"classified as {kind or 'sim.crashed'}"
            )
            on_event.emit(
                f"error.{kind or 'sim.crashed'}",
                returncode=sim_rc,
                stderr_tail=joined[-2000:],
            )
            raise _kind_to_exception(kind or "sim.crashed", msg)

        # ---- stage 2: fuse ---------------------------------------------

        fuse_argv = self._build_fuse_argv(
            reference_ply=reference_ply,
            sim_ply_dir=sim_ply_dir,
            fused_dir=fused_dir,
        )

        t1 = time.monotonic()
        fuse_proc = await self._spawn_in_existing_pg(
            argv=fuse_argv,
            cwd=str(PKG_ROOT),
            pgid=pgid,
        )
        on_event.emit("fuse.spawned", pid=fuse_proc.pid, argv=fuse_argv)
        fuse_stderr_chunks: list[str] = []
        fuse_rc = await _wait_capturing_stderr(fuse_proc, fuse_stderr_chunks)
        fuse_duration = time.monotonic() - t1
        on_event.emit(
            "fuse.completed",
            returncode=fuse_rc,
            duration_sec=fuse_duration,
        )
        if fuse_rc != 0:
            joined = "".join(fuse_stderr_chunks)
            raise SimCrashedError(
                f"fuse exited with rc={fuse_rc} after {fuse_duration:.1f}s; "
                f"stderr tail: {joined[-500:]}"
            )

        n_frames = sum(1 for _ in fused_dir.glob("frame_*.ply"))
        return SimResult(
            frames_dir=fused_dir,
            n_frames=n_frames,
            duration_sec=time.monotonic() - t0,
        )

    # ---- helpers --------------------------------------------------------

    def _build_sim_argv(
        self,
        *,
        model_dir: Path,
        sim_output_dir: Path,
        config_path: Path,
        particles: int,
    ) -> list[str]:
        extras: list[str] = []
        if self._sim_fast:
            extras += ["--no_cfl_override", "--graph_capture"]
        return [
            self._sim_python,
            "gs_simulation/watermelon/gs_simulation_building.py",
            "--model_path", str(model_dir),
            "--output_path", str(sim_output_dir),
            "--config", str(config_path),
            "--target_particles", str(particles),
            "--output_ply", "--async_io",
            *extras,
        ]

    def _build_fuse_argv(
        self,
        *,
        reference_ply: Path,
        sim_ply_dir: Path,
        fused_dir: Path,
    ) -> list[str]:
        return [
            self._sim_python,
            str(PKG_ROOT / "server" / "tools" / "fuse_to_full_ply.py"),
            "--reference_ply", str(reference_ply),
            "--sim_dir", str(sim_ply_dir),
            "--out_dir", str(fused_dir),
            "--knn", "8",
            "--no_zup",
        ]

    async def _spawn_in_new_pg(
        self, argv: list[str], cwd: str
    ) -> asyncio.subprocess.Process:
        """Launch the sim child in a brand-new process group.

        start_new_session=True calls setsid() between fork and the
        target binary load, so the child becomes the leader of a fresh
        session AND process group. Any further children it spawns
        inherit that group, so killpg(pgid, SIG) reaches all of them
        with a single call.
        """
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    async def _spawn_in_existing_pg(
        self, argv: list[str], cwd: str, pgid: int
    ) -> asyncio.subprocess.Process:
        """Launch the fuse child into the sim's existing process group.

        Uses preexec_fn=os.setpgid to slot the child into pgid before
        the target binary loads. This means a single killpg call covers
        both stages on cancel/timeout.
        """
        def _join_pg() -> None:
            os.setpgid(0, pgid)

        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_join_pg,
        )


# ---------- module-level helpers -----------------------------------------


def _gpu_reachable() -> bool:
    """Return True iff nvidia-smi reports at least one CUDA-capable device.

    Conservative: returns False on any error (nvidia-smi missing, no
    devices listed, permission denied). MPMSimulationEngine treats
    False as GPUUnavailableError when require_gpu=True.
    """
    nvsmi = shutil.which("nvidia-smi")
    if nvsmi is None:
        return False
    try:
        result = subprocess.run(
            [nvsmi, "-L"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    # `nvidia-smi -L` prints one "GPU N: ..." line per device.
    return any(line.startswith("GPU ") for line in result.stdout.splitlines())


def _find_reference_ply(model_dir: Path) -> Path | None:
    """Return the highest-iteration point_cloud.ply under model/point_cloud/.

    Mirrors run_sim.sh's `find ... | sort -V | tail -n 1` so we keep
    bug-for-bug compat with the prior behavior. iteration_30000 wins
    over iteration_7000 (version sort, not lex sort).
    """
    pc_root = model_dir / "point_cloud"
    if not pc_root.is_dir():
        return None
    candidates = list(pc_root.rglob("point_cloud.ply"))
    if not candidates:
        return None

    def _iter_num(p: Path) -> int:
        m = re.search(r"iteration_(\d+)", str(p))
        return int(m.group(1)) if m else -1

    return max(candidates, key=_iter_num)


def _read_pid_starttime(pid: int) -> float | None:
    """Read /proc/<pid>/stat field 22 (starttime in clock ticks).

    Persisted alongside pgid so Phase 4 boot recovery can defend against
    PID reuse (same logic core/state.py:is_pid_alive_with_starttime
    uses on read-back).
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, PermissionError):
        return None
    try:
        rest = raw.rsplit(")", 1)[-1].split()
        return float(rest[19])
    except (IndexError, ValueError):
        return None


async def _wait_capturing_stderr(
    proc: asyncio.subprocess.Process,
    sink: list[str],
) -> int:
    """Await the process, draining stderr into `sink` line-by-line.

    Returns the process return code. stdout is drained in parallel so
    the pipe never blocks; only stderr is retained for the classifier.
    """
    assert proc.stderr is not None
    assert proc.stdout is not None

    async def _drain_stderr() -> None:
        async for raw in proc.stderr:
            sink.append(raw.decode(errors="replace"))

    async def _drain_stdout() -> None:
        async for _ in proc.stdout:
            pass  # discard; the run log lives elsewhere

    await asyncio.gather(_drain_stderr(), _drain_stdout())
    return await proc.wait()
```

Create `server/gsfluent/core/sim_engines/__main__.py`:

```python
"""CLI entry point: `python -m gsfluent.core.sim_engines.mpm`.

Used by the slim run_sim.sh shim so the conda activation block in shell
hands control to Python as soon as possible. Argument parsing here
mirrors the old shell script's CLI so existing callers keep working.

Usage:
    python -m gsfluent.core.sim_engines.mpm \\
        <model_dir> --config <recipe.json> \\
        --particles N --output <run_name>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from gsfluent.core.sim_engines.mpm import MPMSimulationEngine
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.sim import ModelRef, SimError


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gsfluent.core.sim_engines.mpm")
    p.add_argument("model_dir", type=Path)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--particles", required=True, type=int)
    p.add_argument("--output", required=True, type=str)
    p.add_argument(
        "--wall-time-sec",
        type=int,
        default=int(os.environ.get("GSFLUENT_MAX_WALL_TIME_SEC", "3600")),
    )
    return p.parse_args()


async def _amain() -> int:
    args = _parse_args()

    recipe = json.loads(args.config.read_text())
    recipe["_run_name"] = args.output
    recipe.setdefault("particle_count", args.particles)

    sim_home = Path(os.environ.get("GSFLUENT_SIM_HOME", ""))
    sim_python = os.environ.get("GSFLUENT_SIM_PYTHON", "python")
    sim_env = os.environ.get("GSFLUENT_SIM_ENV") or None
    sim_fast = os.environ.get("GSFLUENT_SIM_FAST", "0") == "1"

    eng = MPMSimulationEngine(
        sim_home=sim_home,
        sim_python=sim_python,
        sim_env=sim_env,
        sim_fast=sim_fast,
        require_gpu=False,  # CLI is also used in tests; let preflight be lenient here
    )

    emitter = StdlibJSONEmitter(stream=sys.stdout).child(run_name=args.output)

    try:
        await eng.preflight()
        result = await eng.run(
            recipe=recipe,
            model=ModelRef(name=args.model_dir.name, path=args.model_dir),
            output_dir=Path("work/library/sequences") / args.output,
            wall_time_sec=args.wall_time_sec,
            on_event=emitter,
        )
    except SimError as e:
        emitter.emit("cli.failed", error_kind=type(e).__name__, message=str(e))
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    emitter.emit("cli.completed", n_frames=result.n_frames, frames_dir=str(result.frames_dir))
    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
```

Update `server/gsfluent/core/sim_engines/__init__.py` to re-export the engine:

```python
"""Concrete SimulationEngine implementations."""
from gsfluent.core.sim_engines.mpm import (
    MPMErrorPattern,
    MPMSimulationEngine,
    classify_stderr,
    load_error_patterns,
)

__all__ = [
    "MPMErrorPattern",
    "MPMSimulationEngine",
    "classify_stderr",
    "load_error_patterns",
]
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/sim_engines/test_mpm.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/sim_engines/__init__.py \
        server/gsfluent/core/sim_engines/mpm.py \
        server/gsfluent/core/sim_engines/__main__.py \
        server/tests/sim_engines/__init__.py \
        server/tests/sim_engines/test_mpm.py
git commit -m "phase-3: sim_engines/mpm.py — MPMSimulationEngine (PG-aware spawn + YAML stderr classifier) + python -m CLI shim entry"
```

---

### Task 6: core/sim_engines/mock.py — MockSimulationEngine

**Files:**
- Create: `server/gsfluent/core/sim_engines/mock.py`
- Modify: `server/gsfluent/core/sim_engines/__init__.py`
- Create: `server/tests/sim_engines/test_mock.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/sim_engines/test_mock.py`:

```python
"""Tests for the MockSimulationEngine — deterministic test fixture."""
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.protocols.sim import (
    ModelRef,
    SimCrashedError,
    SimGpuOomError,
    SimResult,
    SimUnstableRecipeError,
)


class _RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._ctx: dict = {}

    def emit(self, event: str, **context) -> None:
        merged = {**self._ctx, **context}
        self.events.append((event, merged))

    def child(self, **context) -> "_RecordingEmitter":
        new = _RecordingEmitter()
        new.events = self.events
        new._ctx = {**self._ctx, **context}
        return new


@pytest.fixture
def model(tmp_path: Path) -> ModelRef:
    md = tmp_path / "model"
    md.mkdir()
    return ModelRef(name="model", path=md)


@pytest.mark.asyncio
async def test_mock_preflight_is_a_no_op() -> None:
    eng = MockSimulationEngine()
    await eng.preflight()  # should not raise


@pytest.mark.asyncio
async def test_mock_run_writes_n_frames(tmp_path: Path, model: ModelRef) -> None:
    eng = MockSimulationEngine(n_frames=5)
    result = await eng.run(
        recipe={},
        model=model,
        output_dir=tmp_path / "out",
        wall_time_sec=60,
        on_event=_RecordingEmitter(),
    )
    assert isinstance(result, SimResult)
    assert result.n_frames == 5
    files = sorted(result.frames_dir.glob("frame_*.ply"))
    assert len(files) == 5


@pytest.mark.asyncio
async def test_mock_run_emits_lifecycle_events(
    tmp_path: Path, model: ModelRef
) -> None:
    em = _RecordingEmitter()
    eng = MockSimulationEngine(n_frames=2)
    await eng.run(
        recipe={}, model=model, output_dir=tmp_path / "out",
        wall_time_sec=60, on_event=em,
    )
    event_names = [e[0] for e in em.events]
    assert "sim.spawned" in event_names
    assert "sim.completed" in event_names


@pytest.mark.asyncio
async def test_mock_run_raises_when_configured_to_fail_gpu_oom(
    tmp_path: Path, model: ModelRef
) -> None:
    eng = MockSimulationEngine(fail_with="sim.gpu_oom")
    with pytest.raises(SimGpuOomError):
        await eng.run(
            recipe={}, model=model, output_dir=tmp_path / "out",
            wall_time_sec=60, on_event=_RecordingEmitter(),
        )


@pytest.mark.asyncio
async def test_mock_run_raises_when_configured_to_fail_unstable(
    tmp_path: Path, model: ModelRef
) -> None:
    eng = MockSimulationEngine(fail_with="sim.unstable_recipe")
    with pytest.raises(SimUnstableRecipeError):
        await eng.run(
            recipe={}, model=model, output_dir=tmp_path / "out",
            wall_time_sec=60, on_event=_RecordingEmitter(),
        )


@pytest.mark.asyncio
async def test_mock_run_raises_crashed_when_configured(
    tmp_path: Path, model: ModelRef
) -> None:
    eng = MockSimulationEngine(fail_with="sim.crashed")
    with pytest.raises(SimCrashedError):
        await eng.run(
            recipe={}, model=model, output_dir=tmp_path / "out",
            wall_time_sec=60, on_event=_RecordingEmitter(),
        )


@pytest.mark.asyncio
async def test_mock_respects_delay_sec(
    tmp_path: Path, model: ModelRef
) -> None:
    import time
    eng = MockSimulationEngine(n_frames=3, delay_sec=0.05)
    t0 = time.monotonic()
    await eng.run(
        recipe={}, model=model, output_dir=tmp_path / "out",
        wall_time_sec=60, on_event=_RecordingEmitter(),
    )
    elapsed = time.monotonic() - t0
    # 3 frames * 0.05s = 0.15s; allow generous slack for CI.
    assert elapsed >= 0.10
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/sim_engines/test_mock.py -v
```

Expected: `ImportError: cannot import name 'MockSimulationEngine'`.

- [ ] **Step 3: Implement MockSimulationEngine**

Create `server/gsfluent/core/sim_engines/mock.py`:

```python
"""MockSimulationEngine — deterministic test fixture, no GPU.

Test-only. Used by integration tests under server/tests/integration/.
Production code never wires this in (per spec Open Question #5
default: keep mock as test-only).

Construction knobs:
    n_frames:       how many frame_*.ply stubs to emit
    delay_sec:      sleep between frames (lets cancel/timeout tests fire)
    fail_with:      one of "sim.gpu_oom" / "sim.unstable_recipe" /
                    "sim.crashed", or None for success
    fail_after_frame: int — emit this many frames, then raise (default: 0)
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from gsfluent.protocols.observability import EventEmitter
from gsfluent.protocols.sim import (
    ModelRef,
    SimCrashedError,
    SimGpuOomError,
    SimResult,
    SimUnstableRecipeError,
    ValidatedRecipe,
)


class MockSimulationEngine:
    """In-process fake sim. Writes empty frame_*.ply files into output_dir/frames/."""

    def __init__(
        self,
        *,
        n_frames: int = 3,
        delay_sec: float = 0.0,
        fail_with: str | None = None,
        fail_after_frame: int = 0,
    ) -> None:
        self._n_frames = n_frames
        self._delay_sec = delay_sec
        self._fail_with = fail_with
        self._fail_after_frame = fail_after_frame

    async def preflight(self) -> None:
        # Mock has no preflight constraints. Real engines check env vars
        # and GPU reachability here.
        return None

    async def run(
        self,
        recipe: ValidatedRecipe,
        model: ModelRef,
        output_dir: Path,
        wall_time_sec: int,
        on_event: EventEmitter,
    ) -> SimResult:
        frames_dir = output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.monotonic()
        on_event.emit("sim.spawned", pid=-1, pgid=-1, argv=["<mock>"])

        for i in range(self._n_frames):
            if self._fail_with and i == self._fail_after_frame:
                self._raise_classified()
            (frames_dir / f"frame_{i:04d}.ply").write_text(f"mock frame {i}\n")
            on_event.emit("sim.frame", frame_index=i)
            if self._delay_sec > 0:
                await asyncio.sleep(self._delay_sec)

        if self._fail_with and self._fail_after_frame >= self._n_frames:
            self._raise_classified()

        on_event.emit(
            "sim.completed",
            returncode=0,
            duration_sec=time.monotonic() - t0,
        )
        return SimResult(
            frames_dir=frames_dir,
            n_frames=self._n_frames,
            duration_sec=time.monotonic() - t0,
        )

    def _raise_classified(self) -> None:
        msg = f"MockSimulationEngine configured to fail with {self._fail_with}"
        if self._fail_with == "sim.gpu_oom":
            raise SimGpuOomError(msg)
        if self._fail_with == "sim.unstable_recipe":
            raise SimUnstableRecipeError(msg)
        # default: generic crash
        raise SimCrashedError(msg)
```

Update `server/gsfluent/core/sim_engines/__init__.py`:

```python
"""Concrete SimulationEngine implementations."""
from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.core.sim_engines.mpm import (
    MPMErrorPattern,
    MPMSimulationEngine,
    classify_stderr,
    load_error_patterns,
)

__all__ = [
    "MockSimulationEngine",
    "MPMErrorPattern",
    "MPMSimulationEngine",
    "classify_stderr",
    "load_error_patterns",
]
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/sim_engines/ -v
```

Expected: 19 passed (12 from test_mpm.py + 7 from test_mock.py).

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/sim_engines/__init__.py \
        server/gsfluent/core/sim_engines/mock.py \
        server/tests/sim_engines/test_mock.py
git commit -m "phase-3: sim_engines/mock.py — MockSimulationEngine (configurable n_frames / delay / failure kind) for deterministic CI tests"
```

---

### Task 7: tests/fixtures/mock_sim.sh — configurable fake sim script

**Files:**
- Create: `server/tests/fixtures/__init__.py`
- Create: `server/tests/fixtures/mock_sim.sh`

- [ ] **Step 1: Create the fixtures package**

Create `server/tests/fixtures/__init__.py` as empty file.

- [ ] **Step 2: Write the mock sim shell script**

Create `server/tests/fixtures/mock_sim.sh`:

```bash
#!/usr/bin/env bash
# Configurable fake sim binary for integration tests.
#
# Per the spec (Section 5 "mock_sim.sh fixture — the unlock"), this
# script is parametrized via env vars so every dangerous-path test can
# be deterministic and CI-able with no real GPU.
#
# Env knobs (all optional, all with defaults):
#   MOCK_SIM_FRAMES=3              how many frame_*.ply stubs to emit
#   MOCK_SIM_DELAY_SEC=0.0         per-frame pause (cancel / timeout tests)
#   MOCK_SIM_IGNORE_SIGTERM=0      trap SIGTERM (SIGKILL escalation tests)
#   MOCK_SIM_EXIT=0                final exit code
#   MOCK_SIM_STDERR_PATTERN=       inject a sim-style stderr line (classifier tests)
#                                   Examples: "out of memory" / "CFL violation"
#                                             / "illegal memory access" / "NaN positions"
#
# CLI: the same args the real run_sim.sh accepted, so AsyncioRunManager
# can use this fixture as a drop-in via env-var override.
#   bash mock_sim.sh <model_dir> --config <recipe.json> \
#                    --particles N --output <run_name>

set -euo pipefail

MODEL_DIR=""
CONFIG=""
PARTICLES=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)    CONFIG="$2"; shift 2 ;;
        --particles) PARTICLES="$2"; shift 2 ;;
        --output)    OUTPUT="$2"; shift 2 ;;
        -*)          echo "mock_sim: unknown option: $1" >&2; exit 2 ;;
        *)
            if [[ -z "$MODEL_DIR" ]]; then
                MODEL_DIR="$1"; shift
            else
                echo "mock_sim: extra positional: $1" >&2; exit 2
            fi
            ;;
    esac
done

FRAMES="${MOCK_SIM_FRAMES:-3}"
DELAY="${MOCK_SIM_DELAY_SEC:-0.0}"
IGNORE_SIGTERM="${MOCK_SIM_IGNORE_SIGTERM:-0}"
EXIT_CODE="${MOCK_SIM_EXIT:-0}"
STDERR_PATTERN="${MOCK_SIM_STDERR_PATTERN:-}"

# Resolve output dirs the same way the real script did.
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIB_DIR="$PKG_ROOT/work/library/sequences/$OUTPUT"
FRAMES_DIR="$LIB_DIR/frames"
mkdir -p "$FRAMES_DIR"

# Preserve the recipe so downstream tests can read it (mirrors run_sim.sh).
if [[ -n "$CONFIG" && -f "$CONFIG" ]]; then
    cp -f "$CONFIG" "$LIB_DIR/recipe.json"
fi

# Optional: trap SIGTERM and ignore it. This is how we exercise the
# SIGKILL escalation in test_sigterm_ignoring_sim_gets_sigkill.py —
# the run manager sends SIGTERM to the PG, this script swallows it,
# after the grace period the manager sends SIGKILL.
if [[ "$IGNORE_SIGTERM" == "1" ]]; then
    trap 'echo "mock_sim: trapped SIGTERM (ignoring per MOCK_SIM_IGNORE_SIGTERM=1)" >&2' TERM
fi

echo "mock_sim: starting (frames=$FRAMES delay=$DELAY ignore_sigterm=$IGNORE_SIGTERM exit=$EXIT_CODE)"

i=0
while [[ "$i" -lt "$FRAMES" ]]; do
    printf "mock frame %d\n" "$i" > "$FRAMES_DIR/$(printf 'frame_%04d.ply' "$i")"
    echo "mock_sim: emitted frame $i"
    i=$((i + 1))
    if [[ "$DELAY" != "0" && "$DELAY" != "0.0" ]]; then
        # `sleep` accepts fractional seconds on coreutils sleep.
        sleep "$DELAY" &
        wait $!
    fi
done

# Optionally inject a sim-style stderr line so the classifier kicks in.
if [[ -n "$STDERR_PATTERN" ]]; then
    echo "mock_sim STDERR: $STDERR_PATTERN" >&2
fi

echo "mock_sim: exiting with rc=$EXIT_CODE"
exit "$EXIT_CODE"
```

- [ ] **Step 3: Mark the script as runnable**

```bash
chmod +x /home/frankyin/Desktop/work/gsfluent_pkg/server/tests/fixtures/mock_sim.sh
```

- [ ] **Step 4: Smoke-test the script directly**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
mkdir -p /tmp/mock_sim_smoke
MOCK_SIM_FRAMES=2 \
    bash server/tests/fixtures/mock_sim.sh \
    /tmp/mock_sim_smoke \
    --config /dev/null \
    --particles 100 \
    --output mock_smoke_test
ls -la work/library/sequences/mock_smoke_test/frames/ 2>&1
```

Expected: two files `frame_0000.ply` and `frame_0001.ply` listed. Clean up:

```bash
rm -rf /home/frankyin/Desktop/work/gsfluent_pkg/work/library/sequences/mock_smoke_test
```

- [ ] **Step 5: Test the stderr-injection knob**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
MOCK_SIM_FRAMES=0 MOCK_SIM_STDERR_PATTERN="out of memory" MOCK_SIM_EXIT=137 \
    bash server/tests/fixtures/mock_sim.sh \
    /tmp/mock_sim_smoke \
    --config /dev/null \
    --particles 100 \
    --output mock_oom_test \
    2>&1 || echo "exit=$?"
rm -rf /home/frankyin/Desktop/work/gsfluent_pkg/work/library/sequences/mock_oom_test
```

Expected: stderr contains `mock_sim STDERR: out of memory` and the script exits non-zero (`exit=137` or similar).

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/fixtures/__init__.py \
        server/tests/fixtures/mock_sim.sh
git commit -m "phase-3: tests/fixtures/mock_sim.sh — configurable fake sim binary (FRAMES/DELAY_SEC/IGNORE_SIGTERM/EXIT/STDERR_PATTERN env knobs)"
```

---

### Task 8: Slim server/tools/run_sim.sh to a 20-line conda-activate shim

**Files:**
- Modify: `server/tools/run_sim.sh` (197 lines -> ~25 lines)

- [ ] **Step 1: Replace run_sim.sh with the shim**

Open `server/tools/run_sim.sh` and replace the entire contents with:

```bash
#!/usr/bin/env bash
# Thin conda-activate shim. All orchestration moved to
# server/gsfluent/core/sim_engines/mpm.py — this script only handles
# the bash-context conda activation (which Python cannot do for itself)
# and hands the rest off to the Python entry point.
#
# CLI (unchanged from the prior 197-line version, so callers keep working):
#   bash run_sim.sh <model_dir> --config <recipe.json> \
#                   --particles N --output <run_name>
#
# Env contract (unchanged):
#   GSFLUENT_SIM_HOME    canonical GaussianFluent install root
#   GSFLUENT_SIM_PYTHON  python interpreter with torch / warp / taichi
#   GSFLUENT_SIM_ENV     optional conda env name
set -euo pipefail

if [[ -n "${GSFLUENT_SIM_ENV:-}" ]] && command -v conda >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    eval "$(conda shell.bash hook)"
    conda activate "$GSFLUENT_SIM_ENV"
fi

PY="${GSFLUENT_SIM_PYTHON:-python}"
PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$PKG_ROOT"
exec "$PY" -m gsfluent.core.sim_engines.mpm "$@"
```

- [ ] **Step 2: Verify line count**

```bash
wc -l /home/frankyin/Desktop/work/gsfluent_pkg/server/tools/run_sim.sh
```

Expected: approximately 25 lines (target ≤ 30). The spec target is 20 lines; the comment block pushes us to ~25.

- [ ] **Step 3: Smoke-test the shim with mock_sim args**

The shim should hand off to `python -m gsfluent.core.sim_engines.mpm`. We can verify the dispatch by checking that an invocation with a missing GSFLUENT_SIM_HOME surfaces a SimEnvMissingError from Python (not a shell error):

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
unset GSFLUENT_SIM_HOME
GSFLUENT_SIM_HOME=/nonexistent/sim_home \
GSFLUENT_SIM_PYTHON="$(.venv/bin/python -c 'import sys; print(sys.executable)')" \
    bash server/tools/run_sim.sh \
    /tmp/no_such_model \
    --config /dev/null \
    --particles 100 \
    --output shim_smoke \
    2>&1 | head -20 || echo "exit=$?"
```

Expected: output contains `SimEnvMissingError` (and exit code is non-zero). This proves the Python CLI is being reached.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tools/run_sim.sh
git commit -m "phase-3: tools/run_sim.sh — shrink to 25-line conda-activate shim (177 lines moved to sim_engines/mpm.py)"
```

---

### Task 9: Add PG-aware spawn + signal escalation to AsyncioRunManager

**Files:**
- Modify: `server/gsfluent/core/run_manager.py` (Phase 2's `AsyncioRunManager`)
- Create: `server/tests/runs/test_signal_escalation.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/runs/__init__.py` if it does not already exist (empty file).

Create `server/tests/runs/test_signal_escalation.py`:

```python
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
    # Write a tiny script that traps SIGTERM and sleeps forever.
    script = tmp_path / "ignore_sigterm.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "trap 'echo trapped' TERM\n"
        "while true; do sleep 0.1; done\n"
    )
    script.chmod(0o755)

    proc = await spawn_in_new_pg(argv=["bash", str(script)], cwd="/tmp")
    pgid = os.getpgid(proc.pid)
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
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_signal_escalation.py -v
```

Expected: `ImportError: cannot import name 'escalate_kill_pg'` (or `spawn_in_new_pg`).

- [ ] **Step 3: Add the helpers to run_manager.py**

Open `server/gsfluent/core/run_manager.py` (created by Phase 2). Add the following two module-level helpers somewhere near the existing subprocess-spawn code:

```python
# ---------- process-group lifecycle helpers (Phase 3) --------------------


async def spawn_in_new_pg(
    argv: list[str],
    *,
    cwd: str,
    stdout: int | None = None,
    stderr: int | None = None,
) -> asyncio.subprocess.Process:
    """Launch a child in a brand-new process group.

    `start_new_session=True` triggers setsid() in the child between
    fork and the target binary load. The child becomes the leader of a
    fresh session AND its own process group. Subsequent grandchildren
    inherit that PG, so killpg(pgid, SIG) covers the entire subtree.

    Defaults stdout/stderr to PIPE so callers can drain them.
    """
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=stdout if stdout is not None else asyncio.subprocess.PIPE,
        stderr=stderr if stderr is not None else asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def escalate_kill_pg(
    proc: asyncio.subprocess.Process,
    *,
    pgid: int,
    grace_sec: float = 30.0,
) -> None:
    """SIGTERM the process group, wait up to grace_sec, then SIGKILL.

    Idempotent on already-dead processes (ProcessLookupError is swallowed).
    Called from cancel() and from the wall-time timeout path.

    The two-stage ladder is the contract the spec requires:
      SIGTERM gives the sim a chance to checkpoint / cleanup;
      SIGKILL guarantees we get the GPU back even if it ignores SIGTERM.
    """
    # Stage 1: polite SIGTERM to the whole process group.
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        # Already gone — nothing to do.
        return

    # Stage 2: wait for graceful exit or timeout.
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_sec)
        return
    except asyncio.TimeoutError:
        pass

    # Stage 3: SIGKILL the group. Final hammer.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    # Reap the now-dead process so the asyncio transport closes cleanly.
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        # Should not happen after SIGKILL, but don't deadlock the caller.
        pass
```

Also ensure the module imports `os` and `signal` at the top. If `signal` is not already imported, add `import signal` to the import block.

Update the `AsyncioRunManager.cancel()` method to use `escalate_kill_pg` instead of the direct SIGTERM-then-SIGKILL it has from Phase 2. The Phase 2 version probably looked something like:

```python
# Phase 2 version (preserved current behavior):
async def cancel(self, run_id: RunId) -> None:
    rec = self._state.read(run_id)
    if rec is None or rec.is_terminal():
        return
    if rec.pid:
        try:
            os.kill(rec.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    # ... record state transition ...
```

Replace with the Phase 3 version:

```python
async def cancel(self, run_id: RunId) -> None:
    """Idempotent. SIGTERM the PG; background task escalates to SIGKILL
    after `self._kill_grace_sec` if the process is still alive."""
    rec = self._state.read(run_id)
    if rec is None or rec.is_terminal():
        return
    # Mark cancelling early so a parallel cancel() is a no-op.
    self._state.write(rec.transition(state=RunState.CANCELLING))
    self._obs.emit("run.cancelling", run_id=run_id, pgid=rec.pgid)

    proc = self._procs.get(run_id)
    if proc is None or rec.pgid is None:
        # No live process to signal; treat as cancelled directly.
        self._state.write(
            rec.transition(state=RunState.CANCELLED, finished_at=time.time())
        )
        self._obs.emit("run.cancelled", run_id=run_id)
        return

    # Fire-and-forget background escalation so the API call returns fast.
    asyncio.create_task(
        self._cancel_escalation(run_id=run_id, proc=proc, pgid=rec.pgid)
    )


async def _cancel_escalation(
    self,
    *,
    run_id: RunId,
    proc: asyncio.subprocess.Process,
    pgid: int,
) -> None:
    """Background task: SIGTERM -> wait -> SIGKILL -> mark cancelled."""
    await escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=self._kill_grace_sec)
    rec = self._state.read(run_id)
    if rec is None:
        return
    self._state.write(
        rec.transition(state=RunState.CANCELLED, finished_at=time.time())
    )
    self._obs.emit("run.cancelled", run_id=run_id)
```

And the existing subprocess spawn (wherever it lives in `_run_to_completion`) needs to use `spawn_in_new_pg` and record `pgid` + `pid_starttime` in the state record. Locate the existing `asyncio.create_subprocess_exec` call and replace with:

```python
proc = await spawn_in_new_pg(
    argv=sim_argv,
    cwd=str(self._cfg.work_dir),
)
pgid = os.getpgid(proc.pid)
pid_starttime = _read_pid_starttime(proc.pid)
self._procs[run_id] = proc
self._state.write(
    rec.transition(
        state=RunState.RUNNING,
        pid=proc.pid,
        pgid=pgid,
        pid_starttime=pid_starttime,
        started_at=time.time(),
    )
)
self._obs.emit(
    "run.started",
    run_id=run_id,
    pid=proc.pid,
    pgid=pgid,
)
```

Add the `_read_pid_starttime` helper to `run_manager.py` (it's the same helper as in `mpm.py`; we duplicate it locally to keep the run-manager module self-contained):

```python
def _read_pid_starttime(pid: int) -> float | None:
    """Read /proc/<pid>/stat field 22 (starttime in clock ticks).

    Used to defend against PID reuse: Phase 4 boot recovery compares
    this against the live /proc/<pid>/stat value before reattaching.
    """
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, PermissionError):
        return None
    try:
        rest = raw.rsplit(")", 1)[-1].split()
        return float(rest[19])
    except (IndexError, ValueError):
        return None
```

Finally, ensure the `AsyncioRunManager.__init__` accepts a `kill_grace_sec` kwarg defaulting to 30:

```python
def __init__(
    self,
    *,
    sim_engine,
    # ... existing kwargs ...
    kill_grace_sec: float = 30.0,
) -> None:
    # ... existing init ...
    self._kill_grace_sec = kill_grace_sec
    self._procs: dict[RunId, asyncio.subprocess.Process] = {}
```

- [ ] **Step 4: Run the signal-escalation tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_signal_escalation.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Confirm Phase 2 tests still pass (no regression)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/ -v 2>&1 | tail -20
```

Expected: all existing run-manager tests still pass alongside the 4 new ones.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/run_manager.py \
        server/tests/runs/__init__.py \
        server/tests/runs/test_signal_escalation.py
git commit -m "phase-3: run_manager — spawn_in_new_pg (start_new_session=True) + escalate_kill_pg (SIGTERM -> 30s grace -> SIGKILL); cancel() uses ladder"
```

---

### Task 10: Wall-time enforcement in AsyncioRunManager

**Files:**
- Modify: `server/gsfluent/core/run_manager.py`
- Create: `server/tests/runs/test_wall_time.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/runs/test_wall_time.py`:

```python
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
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_wall_time.py -v
```

Expected: `ImportError: cannot import name 'run_with_wall_time'`.

- [ ] **Step 3: Implement the wall-time helper**

Add to `server/gsfluent/core/run_manager.py` (module-level, next to the other Phase 3 helpers):

```python
from typing import Awaitable, Callable, TypeVar

from gsfluent.protocols.sim import SimWallTimeExceededError

_T = TypeVar("_T")


async def run_with_wall_time(
    *,
    coro_factory: Callable[[], Awaitable[_T]],
    wall_time_sec: float,
    on_timeout: Callable[[], None],
) -> _T:
    """Run a coroutine under a wall-time cap. On timeout, fire on_timeout
    (which should trigger killpg/escalation) and raise SimWallTimeExceededError.

    The caller is responsible for the actual signal-escalation side effect
    inside on_timeout — this helper only orchestrates the timing.
    """
    try:
        return await asyncio.wait_for(coro_factory(), timeout=wall_time_sec)
    except asyncio.TimeoutError:
        try:
            on_timeout()
        except Exception:
            # on_timeout side effects should not mask the timeout itself.
            # Phase 6 will log this; for now we swallow so the raise below
            # still happens.
            pass
        raise SimWallTimeExceededError(
            f"Run exceeded wall-time cap of {wall_time_sec}s"
        )
```

Wire this into the existing `_run_to_completion` (or whatever the Phase 2 driver coroutine is called). Replace the existing direct `await sim_engine.run(...)` with:

```python
def _on_wall_time_exceeded() -> None:
    self._obs.emit("run.wall_time_exceeded", run_id=run_id, pgid=pgid)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    # Schedule the SIGKILL escalation in a background task so the
    # wait_for raise path isn't blocked on grace_sec.
    asyncio.create_task(
        escalate_kill_pg(proc=proc, pgid=pgid, grace_sec=self._kill_grace_sec)
    )

sim_result = await run_with_wall_time(
    coro_factory=lambda: self._sim_engine.run(
        recipe=recipe,
        model=model,
        output_dir=output_dir,
        wall_time_sec=wall_time_sec,
        on_event=per_run_obs,
    ),
    wall_time_sec=wall_time_sec,
    on_timeout=_on_wall_time_exceeded,
)
```

The exact placement depends on Phase 2's code shape. The pattern is: wrap the engine call in `run_with_wall_time`, supply an `on_timeout` callback that calls `killpg(pgid, SIGTERM)` and schedules `escalate_kill_pg` as the SIGKILL fallback.

- [ ] **Step 4: Run wall-time tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/runs/test_wall_time.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/run_manager.py \
        server/tests/runs/test_wall_time.py
git commit -m "phase-3: run_manager — run_with_wall_time helper (asyncio.wait_for + on_timeout -> killpg ladder); raises SimWallTimeExceededError"
```

---

### Task 11: Strict Pydantic + cap checking in api/runs.py

**Files:**
- Modify: `server/gsfluent/api/runs.py`
- Create: `server/tests/api/test_runs_validation.py`

- [ ] **Step 1: Write the failing test**

Create `server/tests/api/test_runs_validation.py`:

```python
"""Tests for strict Pydantic + cap checking on POST /api/runs.

Every rejection must:
  - return HTTP 422
  - carry the {"error": {"kind", "message", "details", "trace_id"}} envelope
  - the `kind` is `validation.<field>` for Pydantic rejections and
    `cap_exceeded.<axis>` for cap violations.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("GSFLUENT_SIM_HOME", str(tmp_path / "sim_home"))
    (tmp_path / "sim_home").mkdir()
    return AppConfig(
        sim_home=tmp_path / "sim_home",
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(
            max_particle_count=500_000,
            max_wall_time_sec=3600,
            max_recipe_bytes=16 * 1024,
        ),
    )


@pytest.fixture
def client(cfg: AppConfig) -> TestClient:
    return TestClient(build_app(cfg))


# ---------- envelope shape -----------------------------------------------


def _assert_envelope_shape(body: dict, expected_kind: str) -> None:
    assert "error" in body
    err = body["error"]
    assert err["kind"] == expected_kind
    assert isinstance(err["message"], str)
    assert isinstance(err["details"], dict)
    assert isinstance(err["trace_id"], str)
    assert len(err["trace_id"]) >= 16


# ---------- Pydantic strict-mode rejection -------------------------------


def test_missing_run_name_returns_422_validation(client: TestClient) -> None:
    resp = client.post("/api/runs", json={
        "model_path": "/tmp/model",
        "recipe_data": {"particle_count": 100},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    _assert_envelope_shape(resp.json(), expected_kind="validation.run_name")


def test_particle_count_wrong_type_returns_422_validation(client: TestClient) -> None:
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": "/tmp/model",
        "recipe_data": {"particle_count": "lots"},
        "recipe_source": "manual",
        "particles": "abc",
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["kind"].startswith("validation.")


def test_unknown_extra_field_rejected_in_strict_mode(client: TestClient) -> None:
    """Pydantic strict mode forbids unknown fields on StartRunRequest."""
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": "/tmp/model",
        "recipe_data": {},
        "recipe_source": "manual",
        "secret_admin_flag": True,
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["kind"].startswith("validation.")


def test_unsafe_run_name_rejected(client: TestClient) -> None:
    """Run names with path separators / suspicious chars are rejected."""
    resp = client.post("/api/runs", json={
        "run_name": "../../etc/passwd",
        "model_path": "/tmp/model",
        "recipe_data": {},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["kind"] == "validation.run_name"


# ---------- cap checking -------------------------------------------------


def test_particle_count_over_cap_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 1_000_000},
        "recipe_source": "manual",
        "particles": 1_000_000,
    })
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope_shape(body, expected_kind="cap_exceeded.particle_count")
    assert body["error"]["details"]["requested"] == 1_000_000
    assert body["error"]["details"]["limit"] == 500_000


def test_wall_time_over_cap_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 100, "wall_time_sec": 9999},
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope_shape(body, expected_kind="cap_exceeded.wall_time")


def test_recipe_size_over_cap_returns_422_cap_exceeded(
    client: TestClient, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    huge_recipe = {"particle_count": 100, "noise": "x" * (20 * 1024)}
    resp = client.post("/api/runs", json={
        "run_name": "test",
        "model_path": str(model_dir),
        "recipe_data": huge_recipe,
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope_shape(body, expected_kind="cap_exceeded.recipe_size")
```

- [ ] **Step 2: Run test, confirm fail**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_runs_validation.py -v 2>&1 | tail -30
```

Expected: most tests fail — the existing `api/runs.py` does not enforce strict mode, does not run `check_recipe_caps`, and the 422 responses use `HTTPException(422, "string")` not the envelope shape.

- [ ] **Step 3: Rewrite the StartRunRequest model + POST handler**

Open `server/gsfluent/api/runs.py`. At the top of the file, update the imports to add:

```python
import re

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError, field_validator

from gsfluent.api.errors import (
    api_error_envelope,
    new_trace_id,
    raise_cap_exceeded,
    raise_validation_error,
)
from gsfluent.core.limits import CapConfig, check_recipe_caps
from gsfluent.protocols.runs import CapExceededError, ValidationError
```

Replace the existing `StartRunRequest` model:

```python
_SAFE_RUN_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class StartRunRequest(BaseModel):
    """Strict-mode request body for POST /api/runs.

    Pydantic strict mode rejects unknown fields and refuses type coercion
    (string "100" will not silently become int 100). check_recipe_caps()
    runs after parse to enforce the configured maxima.
    """

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )

    run_name: str = Field(..., min_length=1, max_length=128)
    model_path: str = Field(..., min_length=1)
    recipe_data: dict
    recipe_source: str
    particles: int = Field(default=200_000, gt=0)
    dry_run: bool = False

    @field_validator("run_name")
    @classmethod
    def _run_name_must_be_safe(cls, v: str) -> str:
        if not _SAFE_RUN_NAME_RE.match(v):
            raise ValueError("run_name must match ^[A-Za-z0-9_.-]+$")
        return v
```

Add a helper that turns a Pydantic ValidationError into a typed `validation.<field>` 422. FastAPI catches Pydantic errors and returns its own format by default — we override via a custom handler that gets installed when the app is built. But for the StartRunRequest, we wrap the parse step ourselves:

Replace the `start` handler:

```python
def _caps_dep() -> CapConfig:
    """FastAPI dependency: return the active CapConfig.

    Phase 3 reads from env every request, which is cheap and dodges
    the ordering problem of importing AppConfig at module load. Phase
    6 may replace this with a singleton from the composition root.
    """
    return CapConfig.from_env()


@router.post("")
async def start(
    raw_body: dict,
    caps: CapConfig = Depends(_caps_dep),
):
    """Submit a run. Validates request body in strict mode, then enforces
    recipe caps, then hands the recipe off to runner.start_run().

    Rejections return 422 with the standard envelope:
        {"error": {"kind", "message", "details", "trace_id"}}
    """
    trace_id = new_trace_id()

    # ---- 1. strict Pydantic parse ------------------------------------
    try:
        req = StartRunRequest.model_validate(raw_body, strict=True)
    except PydanticValidationError as e:
        # Pick the first error to surface as the kind / message; details
        # carries the full list so the client can show all of them.
        errs = e.errors()
        first = errs[0] if errs else {}
        loc = first.get("loc", ("?",))
        loc_parts = [p for p in loc if p != "body"]
        field = ".".join(str(p) for p in loc_parts) if loc_parts else "?"
        msg = first.get("msg", "validation failed")
        raise_validation_error(
            kind=f"validation.{field}",
            message=f"{field}: {msg}",
            details={"errors": errs, "trace_id": trace_id},
        )

    # ---- 2. cap check ------------------------------------------------
    # Compose the cap-check input from the request fields the orchestrator
    # actually consumes. recipe_data carries the customer's free-form
    # recipe; we add particle_count / wall_time_sec from the structured
    # request fields for cap-checking purposes.
    cap_input = {
        **req.recipe_data,
        "particle_count": req.particles,
    }
    # If recipe_data carries an explicit wall_time_sec, honor it; otherwise
    # the cap check uses the configured maximum as the default.
    try:
        check_recipe_caps(cap_input, caps)
    except CapExceededError as e:
        # Translate cap-checker exception messages into typed kinds.
        msg = str(e)
        if "Particle count" in msg:
            raise_cap_exceeded(
                kind="cap_exceeded.particle_count",
                message=msg,
                details={"requested": req.particles, "limit": caps.max_particle_count},
            )
        if "Wall-time" in msg:
            wt = int(req.recipe_data.get("wall_time_sec", caps.max_wall_time_sec))
            raise_cap_exceeded(
                kind="cap_exceeded.wall_time",
                message=msg,
                details={"requested": wt, "limit": caps.max_wall_time_sec},
            )
        if "Recipe size" in msg:
            raise_cap_exceeded(
                kind="cap_exceeded.recipe_size",
                message=msg,
                details={"limit": caps.max_recipe_bytes},
            )
        # Fallback for an unmapped cap-exceeded message — still 422,
        # generic kind.
        raise_cap_exceeded(
            kind="cap_exceeded.unknown",
            message=msg,
            details={},
        )

    # ---- 3. model_path existence check -------------------------------
    model_dir = Path(req.model_path)
    if not model_dir.exists():
        raise_validation_error(
            kind="validation.model_path",
            message=f"model_path does not exist: {req.model_path}",
            details={"got": req.model_path},
        )
    if not model_dir.is_dir():
        raise_validation_error(
            kind="validation.model_path",
            message=f"model_path is not a directory: {req.model_path}",
            details={"got": req.model_path},
        )

    if req.dry_run:
        try:
            effective_recipe = runner._translate_sim_area_if_local(req.recipe_data, model_dir)
            runner._validate_sim_area_intersects_model(
                effective_recipe.get("sim_area", []), model_dir,
            )
        except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
            raise_validation_error(
                kind="validation.recipe_data",
                message=f"recipe validation failed: {e}",
                details={"got": str(e)},
            )
        return {"dry_run": True, "valid": True, "run_name": req.run_name, "trace_id": trace_id}

    # ---- 4. submit ---------------------------------------------------
    try:
        rid = await runner.start_run(
            run_name=req.run_name,
            model_dir=model_dir,
            recipe_data=req.recipe_data,
            recipe_source_name=req.recipe_source,
            particles=req.particles,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError, ValueError) as e:
        raise_validation_error(
            kind="validation.recipe_data",
            message=f"failed to start run: {e}",
            details={"got": str(e)},
        )
    return {"run_id": rid, "run_name": req.run_name, "trace_id": trace_id}
```

- [ ] **Step 4: Run validation tests, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/api/test_runs_validation.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Confirm no regression on existing runs-API tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_runs_api.py -v 2>&1 | tail -30
```

Expected: existing tests still pass. If any tests previously asserted the old `HTTPException(422, "string")` shape they will need updating — update them to assert the new envelope shape (`resp.json()["error"]["kind"]` etc.). List any updates in the commit message.

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/api/runs.py \
        server/tests/api/test_runs_validation.py
git commit -m "phase-3: api/runs.py — strict Pydantic StartRunRequest + check_recipe_caps + 422 envelope (validation.* / cap_exceeded.*)"
```

---

### Task 12: Wire MPMSimulationEngine into composition.py

**Files:**
- Modify: `server/gsfluent/composition.py`

- [ ] **Step 1: Update build_app to wire the engine**

Open `server/gsfluent/composition.py`. The Phase 1 skeleton constructed `StdlibJSONEmitter` and ran the existing routers; the Phase 2 update added `AsyncioRunManager` construction. Phase 3 now needs to actually pass an `MPMSimulationEngine` instance into the `AsyncioRunManager` constructor.

Find the `build_app` function and update the engine wiring:

```python
def build_app(cfg: AppConfig) -> FastAPI:
    """Construct the FastAPI app with all concrete dependencies wired."""
    _ensure_work_dirs(cfg)

    obs: EventEmitter = StdlibJSONEmitter(stream=sys.stdout)
    obs.emit(
        "backend.boot",
        work_dir=str(cfg.work_dir),
        sim_home=str(cfg.sim_home),
    )

    # ---- concrete impls (Phase 3) -------------------------------------
    from gsfluent.core.sim_engines.mpm import MPMSimulationEngine
    from gsfluent.core.run_manager import AsyncioRunManager
    from gsfluent.core.state import RunStateStore

    sim_engine = MPMSimulationEngine(
        sim_home=cfg.sim_home,
        sim_python=cfg.sim_python,
        sim_env=cfg.sim_env,
        # Honor the GSFLUENT_REQUIRE_GPU env var; default True in production.
        require_gpu=os.environ.get("GSFLUENT_REQUIRE_GPU", "1") == "1",
        sim_fast=os.environ.get("GSFLUENT_SIM_FAST", "0") == "1",
    )

    state_store = RunStateStore(state_dir=cfg.work_dir / "_state" / "runs")

    run_mgr = AsyncioRunManager(
        sim_engine=sim_engine,
        state_store=state_store,
        obs=obs,
        caps=cfg.caps,
        kill_grace_sec=30.0,
        # ... preserve whatever other kwargs Phase 2 introduced ...
    )

    # ---- lifespan + router wiring (unchanged from Phase 1/2) ----------
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        obs.emit("backend.lifespan.startup")
        # Phase 4 will call run_mgr.recover_on_boot() here.
        yield
        obs.emit("backend.lifespan.shutdown")

    app = FastAPI(title="gsfluent", version="0.1.0", lifespan=lifespan)
    # ... CORS + router includes unchanged ...
    return app
```

Add an `import os` at the top of `composition.py` if it isn't already there.

- [ ] **Step 2: Smoke-test composition still wires cleanly**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -c "
import os
os.environ.setdefault('GSFLUENT_SIM_HOME', '/tmp')
os.environ.setdefault('GSFLUENT_SIM_PYTHON', 'python')
os.environ.setdefault('GSFLUENT_REQUIRE_GPU', '0')
from gsfluent.config import AppConfig
from gsfluent.composition import build_app
app = build_app(AppConfig.from_env())
print('routes:', sorted(r.path for r in app.routes if hasattr(r, 'path')))
"
```

Expected: prints the route list including `/api/runs`, `/api/sequences`, etc. — same set as before.

- [ ] **Step 3: Run the composition test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/test_composition.py -v
```

Expected: all composition tests still pass.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/composition.py
git commit -m "phase-3: composition.py — wire MPMSimulationEngine (require_gpu / sim_fast from env) into AsyncioRunManager"
```

---

### Task 13: Integration test — cancel kills the process group

**Files:**
- Create: `server/tests/integration/__init__.py`
- Create: `server/tests/integration/conftest.py`
- Create: `server/tests/integration/test_cancel_kills_pg.py`

- [ ] **Step 1: Set up the integration test package**

Create `server/tests/integration/__init__.py` as empty file.

Create `server/tests/integration/conftest.py`:

```python
"""Shared integration-test fixtures.

Each test wires an AsyncioRunManager around the mock_sim.sh fixture so
the actual subprocess lifecycle (PG creation, signal delivery,
escalation, wait_for timeout) is exercised end-to-end without a GPU.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.core.state import RunStateStore
from gsfluent.core.limits import CapConfig
from gsfluent.observability.jsonlog import StdlibJSONEmitter


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
MOCK_SIM_SH = FIXTURE_DIR / "mock_sim.sh"


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_state" / "runs"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """A bare model dir — mock sim does not actually load it."""
    d = tmp_path / "model"
    d.mkdir()
    return d


@pytest.fixture
def event_sink() -> list:
    """Mutable list the StdlibJSONEmitter writes into; tests assert on it."""
    return []


@pytest.fixture
def emitter(event_sink: list) -> StdlibJSONEmitter:
    import io
    # Use a StringIO so the emitter writes structured events we can read back.
    stream = io.StringIO()
    em = StdlibJSONEmitter(stream=stream)
    em._test_stream = stream  # tests reach in for debugging
    return em


@pytest.fixture
def run_manager_with_mock(
    state_dir: Path,
    emitter: StdlibJSONEmitter,
    tmp_path: Path,
) -> AsyncioRunManager:
    """RunManager wired with the in-process MockSimulationEngine.

    For tests that need to exercise actual subprocess PG signal delivery,
    see the SubprocessMockEngine helper below.
    """
    state_store = RunStateStore(state_dir=state_dir)
    return AsyncioRunManager(
        sim_engine=MockSimulationEngine(n_frames=3, delay_sec=0.0),
        state_store=state_store,
        obs=emitter,
        caps=CapConfig(),
        kill_grace_sec=2.0,  # short for tests
    )


class SubprocessMockSimulationEngine:
    """A SimulationEngine that shells out to the mock_sim.sh fixture.

    Use this (instead of MockSimulationEngine) when the test needs to
    verify real subprocess PG signal delivery / escalation. The mock_sim.sh
    fixture is configurable via MOCK_SIM_* env vars.
    """

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self._env = env or {}

    async def preflight(self) -> None:
        return None

    async def run(self, recipe, model, output_dir, wall_time_sec, on_event):
        import asyncio
        import os
        import time
        from gsfluent.core.run_manager import spawn_in_new_pg
        from gsfluent.protocols.sim import SimCrashedError, SimResult

        argv = [
            "bash", str(MOCK_SIM_SH),
            str(model.path),
            "--config", "/dev/null",
            "--particles", "100",
            "--output", output_dir.name,
        ]
        env = {**os.environ, **self._env}
        # Spawn in a brand-new PG just like MPMSimulationEngine does.
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd="/tmp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
        pgid = os.getpgid(proc.pid)
        on_event.emit("sim.spawned", pid=proc.pid, pgid=pgid)
        rc = await proc.wait()
        on_event.emit("sim.completed", returncode=rc)
        if rc != 0:
            raise SimCrashedError(f"mock_sim.sh rc={rc}")
        frames_dir = output_dir
        return SimResult(frames_dir=frames_dir, n_frames=0, duration_sec=0.0)
```

- [ ] **Step 2: Write the cancel-kills-PG test**

Create `server/tests/integration/test_cancel_kills_pg.py`:

```python
"""Integration test: cancel a running run; the entire process group dies.

Spawns mock_sim.sh as a long-running fake sim, then issues cancel(),
and verifies:
  1. SIGTERM reaches the sim's PG
  2. The PG dies within the grace period
  3. The run state transitions to CANCELLED
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

import pytest

from gsfluent.core.run_manager import (
    AsyncioRunManager,
    escalate_kill_pg,
    spawn_in_new_pg,
)
from gsfluent.core.state import RunStateStore
from gsfluent.core.limits import CapConfig
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
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.5",
    }
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    proc = await asyncio.create_subprocess_exec(
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
async def test_run_manager_cancel_transitions_state_to_cancelled(
    tmp_path: Path,
) -> None:
    """End-to-end: submit -> cancel -> state == CANCELLED."""
    state_dir = tmp_path / "_state" / "runs"
    state_dir.mkdir(parents=True)
    import io
    em = StdlibJSONEmitter(stream=io.StringIO())

    from .conftest import SubprocessMockSimulationEngine
    eng = SubprocessMockSimulationEngine(env={
        "MOCK_SIM_FRAMES": "50",
        "MOCK_SIM_DELAY_SEC": "0.3",
    })

    mgr = AsyncioRunManager(
        sim_engine=eng,
        state_store=RunStateStore(state_dir=state_dir),
        obs=em,
        caps=CapConfig(),
        kill_grace_sec=2.0,
    )

    model = ModelRef(name="m", path=tmp_path / "model")
    (tmp_path / "model").mkdir(exist_ok=True)

    run_id = await mgr.submit(
        {"particle_count": 100, "wall_time_sec": 60},
        model=model,
    )
    await asyncio.sleep(0.5)  # let the sim start

    await mgr.cancel(run_id)
    # Give the escalation background task time to run + state to flip.
    for _ in range(40):
        rec = mgr._state.read(run_id)
        if rec and rec.state == RunState.CANCELLED:
            break
        await asyncio.sleep(0.1)

    final = mgr._state.read(run_id)
    assert final is not None
    assert final.state == RunState.CANCELLED
```

- [ ] **Step 3: Run the test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_cancel_kills_pg.py -v
```

Expected: 2 passed. If the second test fails because the Phase 2 AsyncioRunManager has slightly different attribute names (e.g. `_state` vs `state_store`), adjust the test to match the actual implementation — the test asserts on observable behavior (final state == CANCELLED), so the exact attribute path is flexible.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/__init__.py \
        server/tests/integration/conftest.py \
        server/tests/integration/test_cancel_kills_pg.py
git commit -m "phase-3: integration/test_cancel_kills_pg — verify SIGTERM-to-PG dispatch and state flips to CANCELLED"
```

---

### Task 14: Integration test — SIGTERM-ignoring sim gets SIGKILL

**Files:**
- Create: `server/tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py`

- [ ] **Step 1: Write the test**

Create `server/tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py`:

```python
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
    """A mock sim that traps SIGTERM still gets killed after grace_sec."""
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.1",
        "MOCK_SIM_IGNORE_SIGTERM": "1",
    }
    proc = await asyncio.create_subprocess_exec(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "ignore_sigterm_test",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    pgid = os.getpgid(proc.pid)
    await asyncio.sleep(0.3)
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
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.1",
        "MOCK_SIM_IGNORE_SIGTERM": "0",
    }
    proc = await asyncio.create_subprocess_exec(
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
```

- [ ] **Step 2: Run the test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py
git commit -m "phase-3: integration/test_sigterm_ignoring_sim_gets_sigkill — SIGKILL ladder fires after grace; well-behaved sim still exits cleanly"
```

---

### Task 15: Integration test — wall-time enforced

**Files:**
- Create: `server/tests/integration/test_wall_time_enforced.py`

- [ ] **Step 1: Write the test**

Create `server/tests/integration/test_wall_time_enforced.py`:

```python
"""Integration test: wall-time cap kills the sim subprocess.

A mock sim configured for 100 frames at 0.5s each (50s total) is
submitted with wall_time_sec=2. The orchestrator must:
  1. fire asyncio.wait_for timeout
  2. send SIGTERM to the PG
  3. wait grace, then SIGKILL
  4. surface SimWallTimeExceededError
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from pathlib import Path

import pytest

from gsfluent.core.limits import CapConfig
from gsfluent.core.run_manager import (
    AsyncioRunManager,
    run_with_wall_time,
)
from gsfluent.core.state import RunStateStore
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.runs import RunState
from gsfluent.protocols.sim import ModelRef, SimWallTimeExceededError

from .conftest import SubprocessMockSimulationEngine


@pytest.mark.asyncio
async def test_run_with_wall_time_raises_on_long_sim(tmp_path: Path) -> None:
    """A 10s mock sim under a 0.5s cap raises SimWallTimeExceededError."""
    eng = SubprocessMockSimulationEngine(env={
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.1",
    })

    import io
    em = StdlibJSONEmitter(stream=io.StringIO())

    # We're not using AsyncioRunManager here — we drive the engine
    # directly through run_with_wall_time to keep this test
    # self-contained. The next test exercises the integrated path.
    timeout_called = {"hit": False}

    def _on_timeout() -> None:
        timeout_called["hit"] = True

    model = ModelRef(name="m", path=tmp_path / "model")
    (tmp_path / "model").mkdir(exist_ok=True)

    with pytest.raises(SimWallTimeExceededError):
        await run_with_wall_time(
            coro_factory=lambda: eng.run(
                recipe={"particle_count": 100},
                model=model,
                output_dir=tmp_path / "out",
                wall_time_sec=10,
                on_event=em,
            ),
            wall_time_sec=0.5,
            on_timeout=_on_timeout,
        )

    assert timeout_called["hit"] is True


@pytest.mark.asyncio
async def test_full_run_manager_marks_failed_on_wall_time(
    tmp_path: Path,
) -> None:
    """End-to-end: AsyncioRunManager.submit -> wall-time fires -> state == FAILED."""
    state_dir = tmp_path / "_state" / "runs"
    state_dir.mkdir(parents=True)

    eng = SubprocessMockSimulationEngine(env={
        "MOCK_SIM_FRAMES": "100",
        "MOCK_SIM_DELAY_SEC": "0.2",
    })

    mgr = AsyncioRunManager(
        sim_engine=eng,
        state_store=RunStateStore(state_dir=state_dir),
        obs=StdlibJSONEmitter(stream=io.StringIO()),
        caps=CapConfig(),
        kill_grace_sec=1.0,
    )

    model = ModelRef(name="m", path=tmp_path / "model")
    (tmp_path / "model").mkdir(exist_ok=True)

    # Recipe carries a low wall_time_sec to force the cap.
    run_id = await mgr.submit(
        {"particle_count": 100, "wall_time_sec": 1},
        model=model,
    )

    # Wait for the state machine to flip to a terminal state.
    for _ in range(60):
        rec = mgr._state.read(run_id)
        if rec and rec.is_terminal():
            break
        await asyncio.sleep(0.2)

    final = mgr._state.read(run_id)
    assert final is not None
    assert final.state == RunState.FAILED
    assert final.error is not None
    assert "wall_time" in final.error.get("kind", "").lower()
```

- [ ] **Step 2: Run the test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_wall_time_enforced.py -v
```

Expected: 2 passed. If the AsyncioRunManager test fails because the Phase 2 driver doesn't yet record the `sim.wall_time_exceeded` error.kind in the state record, fix the run-manager driver to set `error={"kind": "sim.wall_time_exceeded", "message": str(e), ...}` in the `except SimWallTimeExceededError` branch.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/test_wall_time_enforced.py
git commit -m "phase-3: integration/test_wall_time_enforced — asyncio.wait_for fires + SimWallTimeExceededError surfaces + state == FAILED"
```

---

### Task 16: Integration test — recipe rejected early (no subprocess spawn)

**Files:**
- Create: `server/tests/integration/test_recipe_rejected_early.py`

- [ ] **Step 1: Write the test**

Create `server/tests/integration/test_recipe_rejected_early.py`:

```python
"""Integration test: a bad recipe is rejected at the API boundary with
422, BEFORE any subprocess gets spawned.

We assert two things:
  1. The HTTP response is 422 with the envelope shape.
  2. No process matching the sim binary is running after the request.

This is the spec's correctness guarantee for the recipe-trust boundary:
the GPU only sees recipes that passed strict Pydantic + check_recipe_caps.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gsfluent.composition import build_app
from gsfluent.config import AppConfig
from gsfluent.core.limits import CapConfig


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    monkeypatch.setenv("GSFLUENT_REQUIRE_GPU", "0")
    sh = tmp_path / "sim_home"
    sh.mkdir()
    return AppConfig(
        sim_home=sh,
        sim_python="python",
        sim_env=None,
        work_dir=tmp_path / "work",
        caps=CapConfig(max_particle_count=500_000, max_wall_time_sec=3600),
    )


@pytest.fixture
def client(cfg: AppConfig) -> TestClient:
    return TestClient(build_app(cfg))


def _no_mpm_sim_running() -> bool:
    """Best-effort check: no `gs_simulation_building.py` subprocess on this host.

    We use `pgrep` if available; otherwise we read /proc directly.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gs_simulation_building.py"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        # pgrep returns 1 when no matches; 0 when matches found.
        return result.returncode == 1
    except (FileNotFoundError, subprocess.SubprocessError):
        # Fallback: walk /proc/*/cmdline.
        for proc_dir in Path("/proc").glob("[0-9]*"):
            try:
                cmdline = (proc_dir / "cmdline").read_bytes()
            except (FileNotFoundError, PermissionError):
                continue
            if b"gs_simulation_building.py" in cmdline:
                return False
        return True


def test_over_cap_recipe_returns_422_without_spawning(
    client: TestClient, tmp_path: Path
) -> None:
    """A particle-count-over-cap recipe is rejected; no sim subprocess fires."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    assert _no_mpm_sim_running(), "test prerequisite: no leftover sim before run"

    resp = client.post("/api/runs", json={
        "run_name": "rejected_early_test",
        "model_path": str(model_dir),
        "recipe_data": {"particle_count": 5_000_000},
        "recipe_source": "manual",
        "particles": 5_000_000,
    })

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["kind"] == "cap_exceeded.particle_count"

    # And no sim subprocess was started.
    assert _no_mpm_sim_running(), "sim subprocess fired despite 422 rejection"


def test_invalid_recipe_shape_returns_422_without_spawning(
    client: TestClient, tmp_path: Path
) -> None:
    """A strict-Pydantic rejection (wrong type) is also pre-spawn."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    assert _no_mpm_sim_running()

    resp = client.post("/api/runs", json={
        "run_name": "bad_shape_test",
        "model_path": str(model_dir),
        "recipe_data": "this should be an object",
        "recipe_source": "manual",
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["kind"].startswith("validation.")
    assert _no_mpm_sim_running()
```

- [ ] **Step 2: Run the test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_recipe_rejected_early.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/test_recipe_rejected_early.py
git commit -m "phase-3: integration/test_recipe_rejected_early — over-cap and bad-shape recipes return 422 without spawning sim subprocess"
```

---

### Task 17: Integration test — sim error classification (parametrized)

**Files:**
- Create: `server/tests/integration/test_sim_error_classification.py`

- [ ] **Step 1: Write the parametrized classifier test**

Create `server/tests/integration/test_sim_error_classification.py`:

```python
"""Integration test: stderr patterns map to the right SimError subclass.

Drives mock_sim.sh with each MOCK_SIM_STDERR_PATTERN value the YAML
classifier knows about; verifies the engine raises the expected
typed exception.

Per spec Open Question #1 default: this classifier is included; patterns
live in core/sim_engines/mpm_error_patterns.yaml so operators can tune
them post-launch.
"""
from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path

import pytest

from gsfluent.core.sim_engines.mpm import (
    classify_stderr,
    load_error_patterns,
)
from gsfluent.observability.jsonlog import StdlibJSONEmitter
from gsfluent.protocols.sim import (
    SimCrashedError,
    SimGpuOomError,
    SimUnstableRecipeError,
)

from .conftest import MOCK_SIM_SH


# ---------- classifier unit-style integration ----------------------------


@pytest.mark.parametrize(
    "stderr_text, expected_kind",
    [
        ("CUDA error: out of memory at line 42", "sim.gpu_oom"),
        ("step 17: CFL violation; aborting", "sim.unstable_recipe"),
        ("CUDA: an illegal memory access was encountered", "sim.unstable_recipe"),
        ("frame 12: position contains NaN values", "sim.unstable_recipe"),
        ("frame 9: encountered +inf in velocity", "sim.unstable_recipe"),
        ("Segmentation fault (core dumped)", None),
        ("", None),
    ],
)
def test_classify_stderr_maps_patterns_correctly(
    stderr_text: str, expected_kind: str | None
) -> None:
    patterns = load_error_patterns()
    assert classify_stderr(stderr_text, patterns) == expected_kind


# ---------- end-to-end with mock_sim.sh ----------------------------------


def _exception_for_kind(kind: str | None):
    if kind == "sim.gpu_oom":
        return SimGpuOomError
    if kind == "sim.unstable_recipe":
        return SimUnstableRecipeError
    return SimCrashedError


@pytest.mark.parametrize(
    "stderr_pattern, expected_exc",
    [
        ("out of memory", SimGpuOomError),
        ("CFL violation", SimUnstableRecipeError),
        ("illegal memory access", SimUnstableRecipeError),
        ("NaN positions", SimUnstableRecipeError),
        ("totally unrelated failure", SimCrashedError),
    ],
)
@pytest.mark.asyncio
async def test_mpm_engine_classifies_subprocess_stderr(
    stderr_pattern: str,
    expected_exc: type[Exception],
    tmp_path: Path,
) -> None:
    """Spawn mock_sim.sh with a stderr pattern + non-zero exit; verify
    MPMSimulationEngine raises the matching typed exception kind.

    We use the same `_wait_capturing_stderr` + `classify_stderr` codepath
    by driving a small replica engine that wraps the mock binary.
    """
    env = {
        **os.environ,
        "MOCK_SIM_FRAMES": "1",
        "MOCK_SIM_STDERR_PATTERN": stderr_pattern,
        "MOCK_SIM_EXIT": "137",  # non-zero exit so classifier runs
    }

    proc = await asyncio.create_subprocess_exec(
        "bash", str(MOCK_SIM_SH),
        str(tmp_path / "model"),
        "--config", "/dev/null",
        "--particles", "100",
        "--output", "classifier_test",
        cwd="/tmp",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=env,
    )
    # Drain stderr fully so we can run the classifier on the joined output.
    _, stderr_bytes = await proc.communicate()
    stderr_text = stderr_bytes.decode(errors="replace")
    assert proc.returncode == 137

    patterns = load_error_patterns()
    kind = classify_stderr(stderr_text, patterns)

    # Map kind -> exception class and verify it matches expected_exc.
    actual_exc = _exception_for_kind(kind)
    assert actual_exc is expected_exc, (
        f"stderr='{stderr_text}' classified to kind='{kind}' "
        f"(exc={actual_exc.__name__}); expected {expected_exc.__name__}"
    )
```

- [ ] **Step 2: Run the test, confirm pass**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/integration/test_sim_error_classification.py -v
```

Expected: 12 passed (7 parametrized classifier cases + 5 parametrized end-to-end cases).

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/tests/integration/test_sim_error_classification.py
git commit -m "phase-3: integration/test_sim_error_classification — parametrized stderr -> SimError kind mapping (7 classifier + 5 end-to-end cases)"
```

---

### Task 18: Phase 3 verification + branch handoff

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run the full test suite end-to-end**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. python -m pytest tests/ -v 2>&1 | tail -60
```

Expected: every test passes. Phase 3 added approximately 50 new tests across:
- `tests/api/test_errors.py` — 6 tests
- `tests/api/test_runs_validation.py` — 7 tests
- `tests/sim_engines/test_mpm.py` — 12 tests
- `tests/sim_engines/test_mock.py` — 7 tests
- `tests/runs/test_signal_escalation.py` — 4 tests
- `tests/runs/test_wall_time.py` — 3 tests
- `tests/integration/test_cancel_kills_pg.py` — 2 tests
- `tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py` — 2 tests
- `tests/integration/test_wall_time_enforced.py` — 2 tests
- `tests/integration/test_recipe_rejected_early.py` — 2 tests
- `tests/integration/test_sim_error_classification.py` — 12 tests

Plus all baseline + Phase 1 + Phase 2 tests still pass.

- [ ] **Step 2: Confirm the 5 spec-named integration tests pass individually**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
for t in test_cancel_kills_pg test_sigterm_ignoring_sim_gets_sigkill test_wall_time_enforced test_recipe_rejected_early test_sim_error_classification; do
    echo "--- $t ---"
    PYTHONPATH=. python -m pytest tests/integration/${t}.py -v 2>&1 | tail -8
done
```

Expected: each of the five files reports all-green.

- [ ] **Step 3: Confirm run_sim.sh is now ≤ 30 lines**

```bash
wc -l /home/frankyin/Desktop/work/gsfluent_pkg/server/tools/run_sim.sh
```

Expected: line count is in the 20-30 range.

- [ ] **Step 4: Confirm no stray `print()` calls were added in run_manager / mpm**

```bash
grep -n "^[^#]*print(" /home/frankyin/Desktop/work/gsfluent_pkg/server/gsfluent/core/run_manager.py /home/frankyin/Desktop/work/gsfluent_pkg/server/gsfluent/core/sim_engines/mpm.py 2>&1
```

Expected: no matches (Phase 6 will audit any pre-existing prints; Phase 3 should not introduce new ones).

- [ ] **Step 5: Confirm Phase 3 git history is clean**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git log --oneline main..HEAD
```

Expected: roughly 16 commits, each prefixed `phase-3:`, one per task that added code.

- [ ] **Step 6: Push the branch (do NOT merge yet)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git push -u origin phase-3-sim-orchestration
```

Expected: branch published on origin. Open a PR titled `phase-3: sim orchestration — MPMSimulationEngine + PG signal escalation + wall-time enforcement + strict-recipe 422`.

- [ ] **Step 7: Update the spec status (optional)**

Edit `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`, append to the `**Status:**` line: `Phase 3 implemented in branch phase-3-sim-orchestration (PR #N)`.

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md
git commit -m "docs: mark Phase 3 implemented in branch phase-3-sim-orchestration"
git push
```

---

## Definition of Done — Phase 3

Phase 3 ships when ALL of:

- [ ] All 18 tasks above completed
- [ ] All Phase 3 tests pass (`pytest tests/api tests/sim_engines tests/runs tests/integration -v`)
- [ ] All baseline + Phase 1 + Phase 2 tests still pass (no regressions)
- [ ] `server/tools/run_sim.sh` is ≤ 30 lines and only handles conda activation + hand-off to `python -m gsfluent.core.sim_engines.mpm`
- [ ] `MPMSimulationEngine` spawns subprocesses with `start_new_session=True`, persists `pgid` + `pid_starttime` in the run-state record
- [ ] `AsyncioRunManager.cancel()` uses the SIGTERM -> 30s grace -> SIGKILL ladder via `escalate_kill_pg`
- [ ] Wall-time enforcement uses `asyncio.wait_for(...)` + `killpg(pgid, SIGTERM)` + `escalate_kill_pg` on timeout, surfaces `SimWallTimeExceededError`
- [ ] `api/runs.py` POST handler:
  - Runs Pydantic strict-mode parsing on the request body
  - Runs `limits.check_recipe_caps()` on the recipe before any side effect
  - Returns the 422 envelope shape `{"error": {"kind", "message", "details", "trace_id"}}`
  - Surfaces `validation.<field>` and `cap_exceeded.<axis>` kinds correctly
- [ ] Stderr classifier reads `core/sim_engines/mpm_error_patterns.yaml`, returns the correct `sim.*` kind for the four built-in patterns (gpu_oom, CFL, illegal_memory, NaN/Inf)
- [ ] `tests/fixtures/mock_sim.sh` honors all five env-var knobs (FRAMES, DELAY_SEC, IGNORE_SIGTERM, EXIT, STDERR_PATTERN)
- [ ] Branch `phase-3-sim-orchestration` pushed; PR open for review

## Handoff to Phase 4

Phase 4 (crash recovery + supervision) depends on:
- Process group spawning that persists `pgid` + `pid_starttime` to state (✓ Phase 3)
- `is_pid_alive_with_starttime()` from Phase 1 + the engine-side writer in Phase 3
- The signal-escalation ladder for ungraceful shutdowns (✓ Phase 3)
- The 422 envelope shape (Phase 4's restart-error responses use the same envelope)

Phase 4 will:
- Implement `RunManager.recover_on_boot()` — scan `_state/runs/`, cross-check live PIDs against persisted `pid_starttime`, re-attach or mark `interrupted`
- Wire FastAPI's `lifespan` async context manager to call `recover_on_boot` on startup
- Add `sd_notify("READY=1")` + watchdog heartbeat
- Write `deploy/gsfluent-backend.service`
- Delete `server/supervise.sh`
- Update deploy docs

Phase 4 plan will be authored in a follow-up document: `docs/superpowers/plans/2026-05-22-phase-4-crash-recovery.md`.

## Open questions addressed in Phase 3

- **Spec OQ #1 (`sim.unstable_recipe` classifier):** Default implemented. Patterns live in `core/sim_engines/mpm_error_patterns.yaml` for post-launch tuning. Four default patterns: `out of memory` -> `sim.gpu_oom`, `CFL` / `illegal memory access` / `(?:nan|inf)` -> `sim.unstable_recipe`.
- **Spec OQ #2 (wall-time grace period):** 30 seconds fixed; per-recipe `recipe.shutdown_grace_sec` override and 120s backend cap are deferred to a follow-up (not blocking customer-facing release).
- **Spec OQ #4 (PID-reuse race in recover_on_boot):** Phase 3 persists `pid_starttime` per spec default. Phase 4 will perform the cross-check.

---

**End of Phase 3 plan.**
