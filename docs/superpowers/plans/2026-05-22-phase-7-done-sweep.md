# Phase 7 — Definition-of-Done Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the C slice. Run every test category and capture results, add lint + strict typecheck gates, perform every manual verification from the spec, update README + ARCHITECTURE for the new component layout, resolve the spec's open question on the `sim.unstable_recipe` classifier, and produce a CHANGELOG entry that lists the user-visible changes.

**Architecture:** No new production code lands in Phase 7 — this phase is verification, documentation, and tool configuration. It produces:

- `server/pyproject.toml` extended with `[tool.ruff]` + `[tool.mypy]` blocks; `[project.optional-dependencies].dev` extended with `ruff>=0.5`, `mypy>=1.10`, `hypothesis>=6.100`.
- `.github/workflows/ci.yml` extended (or a sibling `test.yml` added) with `unit`, `integration`, `lint`, `typecheck` jobs.
- `README.md` / `README.en.md` updated: server-admin section now points at systemd (replacing `supervise.sh`); cap-config env-var table added; new component layout summary added.
- `docs/ARCHITECTURE.md` updated: "Components and responsibilities" section refactored to describe the six Protocols + composition root + structured event chain; `supervise.sh` mention replaced by systemd pointer to `deploy/README.md`.
- `CHANGELOG.md` created with a `## [Unreleased] — Backend bulletproofing slice` entry summarizing what shipped across Phases 1-6.
- A short follow-up plan stub (or in-place verification report) for the `sim.unstable_recipe` classifier question.

**Tech Stack:** `ruff>=0.5`, `mypy>=1.10`, `hypothesis>=6.100`, GitHub Actions, `journalctl`, `systemctl`, `curl`. No production-runtime dependency changes.

**Spec reference:** `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md` — especially Section "Definition of done" (lines 690-700) and Section "Open questions" (lines 773-783).

**Plans 1-6 prerequisites assumed met (verify in Task 0):**
- Phase 1: protocols + observability + state + limits + config + composition skeleton landed; ~45 new tests across `tests/protocols/`, `tests/observability/`, `tests/core/`, `tests/test_config.py`, `tests/test_composition.py`.
- Phase 2: `core/codecs/gsq.py`, `core/fusers/knn_kabsch.py`, `storage/filesystem.py`, `core/run_manager.py` extracted; per-impl unit suites and conformance suites running green.
- Phase 3: `core/sim_engines/mpm.py` + `core/sim_engines/mock.py` landed; PG-spawn + SIGTERM→SIGKILL escalation wired; wall-time enforcement via `asyncio.wait_for`; recipe strict-validation + cap-checking in `api/runs.py`; 422 error envelope.
- Phase 4: `RunManager.recover_on_boot()` wired into FastAPI lifespan; `sd_notify` heartbeat; `deploy/gsfluent-backend.service` written; `supervise.sh` deleted; `deploy/README.md` written.
- Phase 5: `Cache-Control`/`ETag`/`If-None-Match` → 304 in `api/sequences.py`; HEAD-skip + Range-resume in `viser_headless._sync_cell_gsq_streaming`; `npz_root`→`cache_root` rename with deprecated aliases.
- Phase 6: Structured events in place across `run_manager.py` + `sim_engines/mpm.py`; `api/health.py` returns real signals; service file health probe updated.

**Phase 7 is plan 7 of 7 — the last one.** It produces the ship signal.

---

## File Structure

### Modified files (Phase 7)

```
README.md                                  ← rewrite "服务端运维" section for systemd;
                                             add cap-config env-var table; mention six-protocol layout
README.en.md                               ← mirror the Chinese updates (server admin → systemd, caps, layout)
docs/ARCHITECTURE.md                       ← "Components and responsibilities" rewrite for six Protocols +
                                             composition root + structured events; supervise.sh → systemd
server/pyproject.toml                      ← add [tool.ruff], [tool.mypy], extend [dev] deps
.github/workflows/ci.yml                   ← add gsfluent-backend lint/typecheck/unit/integration jobs
                                             (NEW jobs; existing api-tests + frontend-build untouched)
```

### New files (Phase 7)

```
CHANGELOG.md                               ← create from scratch with Keep-a-Changelog layout;
                                             populate with the bulletproofing slice's user-visible changes
docs/superpowers/phase-7-verification-log.md ← scratchpad for capturing journalctl excerpts,
                                             systemctl status output, ps verifications, etc.
                                             (an evidence log, not user-facing docs)
```

### Files NOT modified in Phase 7

```
server/gsfluent/                           ← no production code changes;
                                             any ruff/mypy violations introduced by Phases 1-6 get fixed
                                             via targeted edits as needed (Tasks 11-12)
server/tools/                              ← no behavior changes; lint/typecheck-only fixes if violations
deploy/gsfluent-backend.service            ← Phase 4 artifact; Phase 7 only verifies the install works
deploy/README.md                           ← Phase 4 artifact; Phase 7 only refers users to it
```

---

## Tasks

### Task 0: Branch + prerequisite verification

**Files:**
- No file edits in this task. Verification + branch creation only.

- [ ] **Step 1: Create the phase branch from a base that already includes Phases 1-6**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git fetch origin
git checkout main
git pull --ff-only
git checkout -b phase-7-done-sweep
```

Expected: `Switched to a new branch 'phase-7-done-sweep'`. If `git pull` reports diverged history, halt and confirm with the operator which branch carries Phases 1-6 (Phase 6 must be merged or rebased into the base before Phase 7 starts).

- [ ] **Step 2: Verify the six Protocols exist**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -c "
from gsfluent.protocols import (
    EventEmitter, Storage, CacheCodec, Fuser, SimulationEngine, RunManager
)
print('all six protocols importable')
"
```

Expected: `all six protocols importable`. If `ImportError`, halt — Phase 1 was not landed in this base.

- [ ] **Step 3: Verify the concrete impls from Phase 2-3 exist**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -c "
from gsfluent.core.run_manager import AsyncioRunManager
from gsfluent.core.sim_engines.mpm import MPMSimulationEngine
from gsfluent.core.sim_engines.mock import MockSimulationEngine
from gsfluent.core.fusers.knn_kabsch import KNNKabschFuser
from gsfluent.core.codecs.gsq import GSQCodec
from gsfluent.storage.filesystem import FilesystemStorage
print('all concrete impls importable')
"
```

Expected: `all concrete impls importable`. If any `ImportError`, halt — the corresponding earlier phase did not land.

- [ ] **Step 4: Verify `supervise.sh` is gone (Phase 4 deletion)**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
test ! -f server/supervise.sh && echo "supervise.sh deleted (Phase 4 OK)" || echo "supervise.sh STILL EXISTS — Phase 4 did not land"
test -f deploy/gsfluent-backend.service && echo "systemd unit exists (Phase 4 OK)" || echo "systemd unit MISSING — Phase 4 did not land"
test -f deploy/README.md && echo "deploy/README.md exists (Phase 4 OK)" || echo "deploy/README.md MISSING — Phase 4 did not land"
```

Expected: all three OK lines. Halt on any miss.

- [ ] **Step 5: Capture the baseline test count**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/ --collect-only -q 2>&1 | tail -5
```

Record the collected-tests number in `docs/superpowers/phase-7-verification-log.md` Step "Baseline test count" (the log file is created in Task 1). Use this number as the floor — Phase 7 must not reduce it.

- [ ] **Step 6: No commit yet — Task 0 is verification only.**

---

### Task 1: Create the Phase 7 verification log file

**Files:**
- Create: `docs/superpowers/phase-7-verification-log.md`

- [ ] **Step 1: Create an evidence-capture scratchpad**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
mkdir -p docs/superpowers
```

Write `docs/superpowers/phase-7-verification-log.md` with this exact starting content:

```markdown
# Phase 7 verification log

Scratchpad for raw outputs captured during the done-sweep. Each task in
`docs/superpowers/plans/2026-05-22-phase-7-done-sweep.md` that produces
evidence (test counts, journalctl excerpts, systemctl status, ps output)
appends here. The PR description summarizes; this file keeps the raw
proof for the next time a similar verification is needed.

---

## Baseline test count

(Filled in Task 0 Step 5.)

---

## Test category results

### Unit tests

(Task 2 output.)

### Protocol conformance tests

(Task 3 output.)

### Integration tests

(Task 4 output.)

### Property tests

(Task 5 output.)

### End-to-end tests

(Task 6 output.)

---

## Lint + typecheck

### ruff

(Task 11 output.)

### mypy --strict

(Task 12 output.)

---

## Manual verifications

### Kill -9 mid-sim → interrupted

(Task 13 output.)

### Happy-path journalctl event chain

(Task 14 output.)

### Fresh systemd install

(Task 15 output.)

### Cap-violating recipe rejected without subprocess

(Task 16 output.)

### Streaming cache hit

(Task 17 output.)

---

## Classifier decision

(Task 18 output.)

---

## Final test re-run

(Task 21 output.)
```

- [ ] **Step 2: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: verification log — scratchpad scaffold for done-sweep evidence"
```

---

### Task 2: Run the unit-test category and capture results

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append output under "### Unit tests")

The spec partitions the test suite into five categories (see "Test pyramid" in spec line ~643): per-impl unit, protocol conformance, integration, property, e2e. Plus the pre-existing baseline tests (12 files from `tests/test_*.py`). Tasks 2-6 run each category and capture the pass count.

- [ ] **Step 1: Run per-impl unit tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest \
    tests/codecs/ \
    tests/sim_engines/ \
    tests/storage/ \
    tests/fusers/ \
    tests/runs/ \
    tests/observability/ \
    tests/api/ \
    tests/core/test_state.py \
    tests/core/test_limits.py \
    tests/test_config.py \
    tests/test_composition.py \
    -v --tb=short 2>&1 | tee /tmp/phase-7-unit.txt | tail -50
```

Expected counts (drawn from spec Section "New tests" line ~336 and Plan 1 Task 13):

| File | Spec-target tests | Source |
|---|---|---|
| `tests/codecs/test_gsq.py` | ~8 (covers bbox edges, fp16 cov-floor, quantization bounds; spec ~100 LOC) | Phase 2 |
| `tests/sim_engines/test_mpm.py` | ~6 (env-var parsing, preflight error classification) | Phase 3 |
| `tests/sim_engines/test_mock.py` | ~6 (mock-sim correctness) | Phase 2/3 |
| `tests/storage/test_filesystem.py` | ~10 (path traversal, atomic rename, range correctness) | Phase 2 |
| `tests/fusers/test_knn_kabsch.py` | ~10 (coord conversion, Kabsch, K-NN degenerate) | Phase 2 |
| `tests/runs/test_asyncio_run_manager.py` | ~15 (state machine, lifecycle, boot recovery) | Phase 2/3/4 |
| `tests/observability/test_jsonlog.py` | 8 (Plan 1 Task 3 — exact count) | Phase 1 |
| `tests/api/test_runs_validation.py` | ~10 (strict-mode rejection, cap 422 shape) | Phase 3 |
| `tests/api/test_sequences_cache_headers.py` | ~8 (ETag, Cache-Control, 304, 206) | Phase 5 |
| `tests/core/test_state.py` | 9 (Plan 1 Task 9 — exact count) | Phase 1 |
| `tests/core/test_limits.py` | 8 (Plan 1 Task 10 — exact count) | Phase 1 |
| `tests/test_config.py` | 5 (Plan 1 Task 11 — exact count) | Phase 1 |
| `tests/test_composition.py` | 4 (Plan 1 Task 12 — exact count) | Phase 1 |

Approximate unit-test floor: **~107 tests**. Floor = sum of Plan-1 exact counts (34) + spec-derived approximate counts (~73 from Phases 2-5). Actual count will be filled in during execution; if collected count is more than 20% below the floor, halt and investigate which impl is missing tests.

- [ ] **Step 2: Append the tail to the verification log**

Append the contents of `/tmp/phase-7-unit.txt` (final summary line plus any failures) under the heading `### Unit tests` in `docs/superpowers/phase-7-verification-log.md`. Include the exact line that looks like `===== N passed in Xs =====`.

- [ ] **Step 3: Confirm the pass line**

The summary line must read `N passed` with no `failed` and no `error`. If any failures, do not commit — fix them first (a unit-test failure here is a regression introduced by Phases 1-6 that earlier phase reviews missed).

- [ ] **Step 4: Commit the log update**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: unit tests — captured pass count and tail in verification log"
```

---

### Task 3: Run protocol-conformance tests and capture results

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Protocol conformance tests")

- [ ] **Step 1: Run conformance suites**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/protocols/ -v --tb=short 2>&1 | tee /tmp/phase-7-protocols.txt | tail -40
```

Expected counts (spec Section "New tests" lines 337-341 — five suites):

| File | Spec-target tests | Source |
|---|---|---|
| `tests/protocols/test_observability_protocol.py` | 4 (Plan 1 Task 2 — exact count) | Phase 1 |
| `tests/protocols/test_storage_protocol.py` | 4 (Plan 1 Task 4 — exact count) | Phase 1 |
| `tests/protocols/test_cache_protocol.py` | 4 (Plan 1 Task 5 — exact count) | Phase 1 |
| `tests/protocols/test_fuse_protocol.py` | 4 (Plan 1 Task 6 — exact count) | Phase 1 |
| `tests/protocols/test_sim_protocol.py` | 4 (Plan 1 Task 7 — exact count) | Phase 1 |
| `tests/protocols/test_runs_protocol.py` | 5 (Plan 1 Task 8 — exact count) | Phase 1 |
| `tests/protocols/test_simulation_engine_conformance.py` | ~6 (preflight, run-to-completion, cancel; spec ~150 LOC) | Phase 2 |
| `tests/protocols/test_fuser_conformance.py` | ~4 (correspondence build, frame fuse; spec ~80 LOC) | Phase 2 |
| `tests/protocols/test_cache_codec_conformance.py` | ~8 (encode/decode round-trip, streaming, edges; spec ~150 LOC) | Phase 2 |
| `tests/protocols/test_storage_conformance.py` | ~6 (put/get/range/stat/exists; spec ~120 LOC) | Phase 2 |
| `tests/protocols/test_run_manager_conformance.py` | ~6 (submit/cancel/status/recover; spec ~150 LOC) | Phase 2/3/4 |

Approximate conformance-test floor: **~55 tests** (25 from Plan 1 stub conformance + ~30 from Phase 2 concrete-impl conformance).

- [ ] **Step 2: Append the tail to the verification log**

Append the test summary line and any details under `### Protocol conformance tests`.

- [ ] **Step 3: Confirm the pass line**

The line must read `N passed`. Conformance failures mean the concrete impl no longer satisfies the Protocol contract — block the merge and reopen Phase 2/3/4 to fix the impl.

- [ ] **Step 4: Commit the log update**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: protocol conformance — captured pass count and tail"
```

---

### Task 4: Run integration tests and capture results

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Integration tests")

- [ ] **Step 1: Run integration tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/integration/ -v --tb=short 2>&1 | tee /tmp/phase-7-integration.txt | tail -60
```

Expected counts (spec Section "New tests" lines 351-357 — seven integration suites):

| File | Spec-target tests | Source |
|---|---|---|
| `tests/integration/test_cancel_kills_pg.py` | ~3 (submit → cancel → PG dead within grace; spec ~80 LOC) | Phase 3 |
| `tests/integration/test_sigterm_ignoring_sim_gets_sigkill.py` | ~2 (escalation works; spec ~80 LOC) | Phase 3 |
| `tests/integration/test_wall_time_enforced.py` | ~3 (timeout fires, `run.failed.sim.wall_time_exceeded`; spec ~80 LOC) | Phase 3 |
| `tests/integration/test_restart_mid_run_recovers.py` | ~4 (state persists, boot reconciles; spec ~100 LOC) | Phase 4 |
| `tests/integration/test_sim_error_classification.py` | ~6 (parametrized stderr → expected error kind; spec ~120 LOC) | Phase 3 / Phase 6 |
| `tests/integration/test_streaming_cache_hit.py` | ~3 (second request uses HEAD, no body downloaded; spec ~80 LOC) | Phase 5 |
| `tests/integration/test_streaming_resume_from_partial.py` | ~3 (Range request, 206 received, decode completes; spec ~100 LOC) | Phase 5 |

Approximate integration-test floor: **~24 tests**. Spec note (line 651): integration suite runtime budget is ~3 minutes. If runtime exceeds 10 minutes, log it but do not block — investigate after merge.

- [ ] **Step 2: Append the tail to the verification log**

Append the summary line + per-test names under `### Integration tests`.

- [ ] **Step 3: Confirm the pass line**

Must read `N passed`. Integration failures here typically mean the `mock_sim.sh` fixture is misconfigured or a real-process side-effect (PG signaling, /proc reads, file locking) regressed. Fix and re-run; do not commit failures.

- [ ] **Step 4: Commit the log update**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: integration tests — captured pass count and per-test names"
```

---

### Task 5: Run property tests and capture results

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Property tests")

- [ ] **Step 1: Confirm Hypothesis is installed**

Property tests use the Hypothesis library, which Plan 2 should have added to `[project.optional-dependencies].dev`. If missing, Phase 2 did not land its property-test deps:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/python -c "import hypothesis; print(f'hypothesis {hypothesis.__version__} installed')"
```

If `ModuleNotFoundError`, install it (this is the one production-adjacent dep change Phase 7 is allowed to make):

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/pip install 'hypothesis>=6.100'
```

Then add it to `pyproject.toml` as part of Task 11's dev-deps edit. Log this in the verification log if it happened.

- [ ] **Step 2: Run property tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/property/ -v --tb=short 2>&1 | tee /tmp/phase-7-property.txt | tail -30
```

Expected counts (spec Section "New tests" lines 358-359 — two property suites):

| File | Spec-target tests | Source |
|---|---|---|
| `tests/property/test_gsq_round_trip.py` | ~2 (Hypothesis: encode→decode preserves data within bounds; spec ~80 LOC) | Phase 2 |
| `tests/property/test_quantization_bounds.py` | ~2 (int16 xyz quantization error bound; spec ~60 LOC) | Phase 2 |

Approximate property-test floor: **~4 tests**. Spec runtime budget: ~2 minutes (Hypothesis examples count is `max_examples=100` default — Phase 2 may have set a lower count for CI speed).

- [ ] **Step 3: Append the tail to the verification log**

Append the summary line under `### Property tests`. If any Hypothesis examples failed, the falsifying example must be reproducible — paste the `Falsifying example:` block from the pytest output into the log so the next agent can re-run it.

- [ ] **Step 4: Confirm the pass line**

Must read `N passed`. Hypothesis failures point at a real correctness bug in the codec or fuser — block the merge and reopen Phase 2.

- [ ] **Step 5: Commit the log update**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: property tests — Hypothesis suite captured (round-trip + quantization bounds)"
```

---

### Task 6: Run e2e tests and capture results

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### End-to-end tests")

- [ ] **Step 1: Run e2e tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/e2e/ -v --tb=short 2>&1 | tee /tmp/phase-7-e2e.txt | tail -30
```

Expected counts (spec Section "New tests" lines 360-361 — two e2e suites):

| File | Spec-target tests | Source |
|---|---|---|
| `tests/e2e/test_happy_path_small.py` | ~2 (submit recipe → completed → fetch .gsq; spec ~80 LOC, uses `MockSimulationEngine`) | Phase 2/3 |
| `tests/e2e/test_recipe_rejected_early.py` | ~2 (422 before any subprocess spawn; spec ~60 LOC) | Phase 3 |

Approximate e2e-test floor: **~4 tests**. Runtime budget per spec: ~30 seconds.

- [ ] **Step 2: Append the tail to the verification log**

Append summary line + per-test names under `### End-to-end tests`.

- [ ] **Step 3: Confirm the pass line**

Must read `N passed`. e2e failure here is the strongest signal of a cross-layer bug — typically the API↔RunManager wiring (Phase 3) or the composition root (Phase 2 follow-up).

- [ ] **Step 4: Commit the log update**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: e2e tests — happy-path and early-rejection captured"
```

---

### Task 7: Run the pre-existing baseline tests (no-regression gate)

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Test category results")

Spec line 696: "Existing `test_runner.py`, `test_runs_api.py`, `test_sequences_import.py`, `test_zup_invariant.py`, etc. still pass (no regressions across the refactor)". This is a separate, explicit Definition-of-Done bullet — give it its own task.

- [ ] **Step 1: Run only the pre-existing baseline tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest \
    tests/test_cells.py \
    tests/test_coord_convert.py \
    tests/test_frame_stream.py \
    tests/test_health.py \
    tests/test_library_smoke.py \
    tests/test_models.py \
    tests/test_recipes.py \
    tests/test_runner.py \
    tests/test_runs_api.py \
    tests/test_schemas.py \
    tests/test_sequences_import.py \
    tests/test_zup_invariant.py \
    -v --tb=short 2>&1 | tee /tmp/phase-7-baseline.txt | tail -40
```

Expected: every test passes — count matches the floor recorded in Task 0 Step 5. Any new failure means a Phase 2-6 refactor broke a back-compat contract.

- [ ] **Step 2: Append the baseline result to the verification log**

Append the summary line under a new subsection `### Pre-existing baseline (no-regression gate)` directly above `### Unit tests`.

- [ ] **Step 3: Confirm count matches floor**

Compare to the Task 0 Step 5 number. If lower, halt and find what broke. If higher (someone added a baseline test), update the recorded floor for the next sweep.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: baseline tests — 12 legacy files re-run, no regressions"
```

---

### Task 8: Run the full suite and capture an aggregate count

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under new section "## Full suite — aggregate")

- [ ] **Step 1: Run everything at once**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/phase-7-all.txt | tail -10
```

Expected: aggregate pass count equals the sum of the per-category counts captured in Tasks 2-7 (within rounding for shared fixtures). Approximate total floor: **~190 tests** (107 unit + 55 conformance + 24 integration + 4 property + 4 e2e + 12 baseline assume average of 1.5 tests per baseline file, so ~18; let actual count govern).

- [ ] **Step 2: Append aggregate to verification log**

Append the final `===== N passed in Xs =====` line under a new heading `## Full suite — aggregate` after the per-category sections.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: full suite aggregate — captured total pass count and runtime"
```

---

### Task 9: Add ruff config to pyproject.toml

**Files:**
- Modify: `server/pyproject.toml`

The spec's "Definition of done" calls for `lint: ruff` (line 684) and `typecheck: mypy --strict on gsfluent/` (line 685). Phase 7 is where they land.

- [ ] **Step 1: Read the current pyproject.toml to scope the edit**

The file is 34 lines (verified at planning time). Open it to confirm no existing `[tool.ruff]` or `[tool.mypy]` blocks; if present, the edit becomes a careful merge instead of an append.

- [ ] **Step 2: Add the `[tool.ruff]` block and extend dev deps**

Edit `server/pyproject.toml`. Replace the `dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27"]` line with:

```toml
dev = [
  "pytest>=8",
  "pytest-asyncio>=0.23",
  "httpx>=0.27",
  "ruff>=0.5",
  "mypy>=1.10",
  "hypothesis>=6.100",
]
```

Then append (at the end of the file, after `[tool.hatch.build.targets.wheel]`):

```toml

[tool.ruff]
line-length = 100
target-version = "py310"
extend-exclude = [
  ".venv",
  "patches",
  "uv.lock",
]

[tool.ruff.lint]
# Conservative starter set: pycodestyle (E), pyflakes (F), isort (I),
# bugbear (B), pyupgrade (UP), comprehensions (C4). No formatting opinion
# beyond line-length — ruff format is opt-in. Ignore E501 (long lines):
# documented format constants and JSON-shape examples regularly exceed 100.
select = ["E", "F", "I", "B", "UP", "C4"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
# Tests routinely have unused fixture-parameter imports flagged by F401
# (pytest fixtures are imported for side effects) and assert-rewriting
# helpers flagged by B011. Allow them.
"tests/**" = ["F401", "F811", "B011"]
# Protocols/__init__.py is a re-export hub; bare imports are intentional.
"gsfluent/protocols/__init__.py" = ["F401"]
"gsfluent/observability/__init__.py" = ["F401"]
"gsfluent/storage/__init__.py" = ["F401"]
"gsfluent/core/sim_engines/__init__.py" = ["F401"]
"gsfluent/core/fusers/__init__.py" = ["F401"]
"gsfluent/core/codecs/__init__.py" = ["F401"]
```

- [ ] **Step 3: Install the new dev deps locally**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/pip install -e '.[dev]'
```

Expected: `Successfully installed ruff-... mypy-... hypothesis-...`. If pip refuses (offline env / no PyPI), document the workaround in the verification log and ask the operator how the box installs deps.

- [ ] **Step 4: Verify ruff is callable**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/ruff --version
```

Expected: `ruff 0.5.x` or higher.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/pyproject.toml
git commit -m "phase-7: pyproject — add [tool.ruff] block + dev deps (ruff, mypy, hypothesis)"
```

---

### Task 10: Add mypy config to pyproject.toml

**Files:**
- Modify: `server/pyproject.toml`

- [ ] **Step 1: Append the `[tool.mypy]` block**

Edit `server/pyproject.toml`. Append (after the `[tool.ruff.lint.per-file-ignores]` block from Task 9):

```toml

[tool.mypy]
python_version = "3.10"
strict = true
# Concrete impls live under gsfluent/; tests are checked less strictly
# because Protocol-conforming stubs deliberately use Any.
files = ["gsfluent"]
plugins = []
# Third-party libs we use without type stubs.
[[tool.mypy.overrides]]
module = [
  "plyfile",
  "plyfile.*",
  "viser",
  "viser.*",
  "watchfiles",
  "watchfiles.*",
]
ignore_missing_imports = true
```

- [ ] **Step 2: Verify mypy is callable**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/mypy --version
```

Expected: `mypy 1.10.x` or higher.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/pyproject.toml
git commit -m "phase-7: pyproject — add [tool.mypy] strict config for gsfluent/ package"
```

---

### Task 11: Run ruff and fix any violations

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### ruff")
- Modify: any `gsfluent/**.py` files that ruff flags

- [ ] **Step 1: Run ruff against the package and tests**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/ruff check gsfluent/ tests/ 2>&1 | tee /tmp/phase-7-ruff.txt | tail -50
```

Expected outcomes (in priority order):
1. `All checks passed!` — done; skip to Step 3.
2. A list of findings of category `F401` (unused imports), `I001` (import sort), `UP006` (`typing.List` → `list`), `B008` (function-call default arg) — these are auto-fixable.
3. Findings that need manual judgment (rare in a green field codebase, but possible).

- [ ] **Step 2: Auto-fix what can be auto-fixed**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/ruff check --fix gsfluent/ tests/ 2>&1 | tail -30
```

Then re-run `ruff check` and inspect any remaining findings.

For each remaining finding, decide:
- True positive → fix the code with `Edit`. Common cases: rename shadowing variables (`B007`), parametrize mutable default args (`B008`), remove unreachable code (`F841`).
- False positive specific to a single file → add a `# noqa: <code>` inline comment with a one-line justification.
- False positive that affects a whole submodule → extend `[tool.ruff.lint.per-file-ignores]` in `pyproject.toml`.

Important: do NOT silence findings wholesale via `--fix-only` or by adding broad `extend-ignore`. The point of this gate is to find and fix real issues.

- [ ] **Step 3: Confirm clean run**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/ruff check gsfluent/ tests/
```

Expected: `All checks passed!`

- [ ] **Step 4: Append the result to the verification log**

Append under `### ruff` in `docs/superpowers/phase-7-verification-log.md`:
- The final ruff invocation and its `All checks passed!` output line.
- A one-line summary of how many violations were fixed and which categories (e.g. "Fixed 7 F401, 3 I001, 1 B008; no manual `# noqa` added.").

- [ ] **Step 5: Commit**

If fixes were made:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/ server/tests/ docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: ruff — clean run; auto-fixed <N> findings (F401/I001/...)"
```

If no fixes were needed, commit just the log:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: ruff — clean run on gsfluent/ and tests/, no fixes needed"
```

---

### Task 12: Run mypy --strict and fix any violations

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### mypy --strict")
- Modify: any `gsfluent/**.py` files that mypy flags

- [ ] **Step 1: Run mypy on the package**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/mypy gsfluent/ 2>&1 | tee /tmp/phase-7-mypy.txt | tail -60
```

Expected outcomes (in priority order):
1. `Success: no issues found in N source files.` — done; skip to Step 3.
2. Findings about missing return annotations, unannotated `**kwargs`, untyped decorators — these are real type debt.
3. Findings about `Any` flowing through Protocols (e.g. `EventEmitter.emit(**context: Any)`) — these are intentional per Plan 1, expected to pass under `strict = true` because `Any` is explicit.

- [ ] **Step 2: Fix findings**

For each finding, decide:
- True positive (missing annotation, wrong type, unreachable code) → fix with `Edit`. Add the missing annotation, narrow the type, etc.
- Forward-reference issue (string-typed Protocol params) → if mypy can't resolve a string type, switch to `from __future__ import annotations` (already used throughout Phase 1) or import the type unconditionally.
- Third-party lib without stubs → add the module to `[[tool.mypy.overrides]] ignore_missing_imports = true` in `pyproject.toml` (the Task 10 block already covers `plyfile`, `viser`, `watchfiles`; extend for new ones).
- Genuine "we can't type this" pattern (rare; e.g. dynamic FastAPI dependency injection) → add `# type: ignore[<error-code>]` with a one-line justification comment.

Important: do NOT switch to `strict = false` or `disable_error_code` wholesale. If a whole error category seems impossible to satisfy, escalate — Phase 7 is the last chance to catch it.

- [ ] **Step 3: Confirm clean run**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/mypy gsfluent/
```

Expected: `Success: no issues found in N source files.`

- [ ] **Step 4: Append the result to the verification log**

Append under `### mypy --strict`:
- The final mypy invocation and its `Success:` line.
- A one-line summary of how many findings were fixed and what categories they touched (e.g. "Fixed 4 missing-return-annotation, 2 untyped-decorator; added 1 type: ignore for FastAPI Depends dynamic injection.").

- [ ] **Step 5: Commit**

If fixes were made:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/ docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: mypy --strict — clean run; fixed <N> annotation findings"
```

If no fixes needed:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: mypy --strict — clean run on gsfluent/, no fixes needed"
```

---

### Task 13: Manual verification — kill -9 mid-sim, restart, run marked `interrupted`

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Kill -9 mid-sim → interrupted")

Spec Section 3 Flow C (line 469) and Definition-of-done bullet (line 697): "Manual: kill -9 the backend mid-sim, restart, run resumes as `interrupted`".

- [ ] **Step 1: Ensure the systemd unit is installed and running**

This task assumes Task 15 (fresh systemd install) has been done OR a previous systemd install on this dev box. If not, do Task 15 first.

```bash
sudo systemctl status gsfluent-backend.service
```

Expected: `Active: active (running) since ...`. If `inactive` or `failed`, run `sudo systemctl start gsfluent-backend.service` and re-check.

- [ ] **Step 2: Find the long-running recipe**

A recipe with `wall_time_sec` around 120-300 (long enough to kill in the middle, short enough to not waste the day).

```bash
ls server/recipes/
```

Pick one that takes meaningful sim time. Example: `server/recipes/wave_basic.json`. Confirm `wall_time_sec` is at least 120 (edit a copy if needed).

- [ ] **Step 3: Submit the recipe via curl**

```bash
# Use the actual local port from gsfluent-backend.service (default 7869).
PORT=7869
RECIPE_NAME=wave_basic  # adjust to whichever recipe was chosen

RUN_ID=$(curl -s -X POST "http://127.0.0.1:${PORT}/api/runs" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg name "$RECIPE_NAME" '{recipe_name: $name}')" \
    | jq -r '.run_id')
echo "Submitted run: $RUN_ID"
```

Note: the exact POST payload shape comes from Phase 3's `api/runs.py`. If `{recipe_name: ...}` is wrong, check `server/gsfluent/api/runs.py` for the request schema and adjust. The minimal viable payload is what the existing `tests/test_runs_api.py` posts.

- [ ] **Step 4: Wait until the run is in `started` state, then kill -9 the backend**

```bash
sleep 10  # allow run to reach started + the sim subprocess to spawn

curl -s "http://127.0.0.1:${PORT}/api/runs/${RUN_ID}" | jq '.state'
# Expected: "started"
```

Find the backend's main PID and send SIGKILL:

```bash
MAIN_PID=$(systemctl show -p MainPID --value gsfluent-backend.service)
echo "Backend MainPID: $MAIN_PID"

sudo kill -9 "$MAIN_PID"
```

systemd will restart the service automatically (per the unit's `Restart=on-failure` or `Restart=always` directive). Wait for the restart:

```bash
sleep 5
sudo systemctl status gsfluent-backend.service --no-pager | head -10
```

Expected: `Active: active (running) since ...` (new start time). Restart count incremented by 1.

- [ ] **Step 5: Read the run record after restart**

```bash
sleep 3  # let the lifespan recover_on_boot() complete

curl -s "http://127.0.0.1:${PORT}/api/runs/${RUN_ID}" | jq '.'
```

Expected:
```json
{
  "id": "<RUN_ID>",
  "state": "interrupted",
  "error": {
    "kind": "internal.backend_restarted",
    "message": "...",
    ...
  },
  ...
}
```

The state must be `interrupted` (NOT `failed`, NOT `running`). The error.kind must be `internal.backend_restarted` per spec line 597.

- [ ] **Step 6: Append the evidence to the verification log**

Append under `### Kill -9 mid-sim → interrupted`:
- The submitted recipe name + RUN_ID.
- The pre-kill `status` output (showing `started`).
- The kill command and `systemctl status` (post-restart) output.
- The post-restart run-record JSON.
- One sentence confirming Flow C contract: "Run X transitioned `started` → `interrupted` with `error.kind = internal.backend_restarted` after SIGKILL+restart, matching spec Section 3 Flow C."

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: manual — kill -9 mid-sim leaves run in 'interrupted' state per Flow C"
```

---

### Task 14: Manual verification — happy-path structured event chain in journalctl

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Happy-path journalctl event chain")

Spec Definition-of-done bullet (line 698): "Manual: `journalctl -u gsfluent-backend -o json | jq` shows structured events for a happy-path run".

- [ ] **Step 1: Pick a short happy-path recipe**

Use a recipe with `wall_time_sec` < 60 — short enough to complete inside the verification window. Example: `server/recipes/quick_smoke.json` (or whichever Phase 3 added as the integration-test recipe). If no short recipe exists, create one in-band using the existing schema and `MockSimulationEngine`:

```bash
# Capture timestamp before submit (used as journalctl filter).
SINCE=$(date -Iseconds)
echo "Filter timestamp: $SINCE"
```

- [ ] **Step 2: Submit the recipe**

```bash
PORT=7869
RUN_ID=$(curl -s -X POST "http://127.0.0.1:${PORT}/api/runs" \
    -H "Content-Type: application/json" \
    -d '{"recipe_name": "quick_smoke"}' \
    | jq -r '.run_id')
echo "Submitted run: $RUN_ID"
```

Wait for completion:

```bash
for i in $(seq 1 30); do
    STATE=$(curl -s "http://127.0.0.1:${PORT}/api/runs/${RUN_ID}" | jq -r '.state')
    echo "[$i] state=$STATE"
    if [ "$STATE" = "completed" ] || [ "$STATE" = "failed" ]; then
        break
    fi
    sleep 2
done
```

Expected: state reaches `completed` within ~60s.

- [ ] **Step 3: Pull the structured event chain from journalctl**

```bash
sudo journalctl -u gsfluent-backend --since "$SINCE" -o cat \
    | grep -E '"event":' \
    | jq -c "select(.run_id == \"$RUN_ID\") | {ts, event}"
```

Expected event sequence (drawn from spec line 100 — the spec calls them out by name):

```
{"ts":"...","event":"run.queued"}
{"ts":"...","event":"run.started"}
{"ts":"...","event":"run.preflight_ok"}
{"ts":"...","event":"sim.started"}
{"ts":"...","event":"sim.completed"}
{"ts":"...","event":"run.simmed"}
{"ts":"...","event":"run.fused"}
{"ts":"...","event":"run.packed"}
{"ts":"...","event":"run.completed"}
```

The exact order, names, and intermediate sim-frame events are owned by Phase 3 + Phase 6. The verification is: `run.queued` appears first, `run.completed` appears last, no `error.*` event in between, and every event carries the same `run_id`.

- [ ] **Step 4: Append the evidence to the verification log**

Append under `### Happy-path journalctl event chain`:
- The recipe name + RUN_ID.
- The exact `journalctl` invocation used.
- The full event chain output (one line per event, with `ts` + `event` fields).
- One sentence confirming: "Happy-path run X emitted `run.queued` → ... → `run.completed` without any `error.*` event; every event carried `run_id=X`."

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: manual — happy-path journalctl chain (run.queued → run.completed) verified"
```

---

### Task 15: Manual verification — fresh systemd install + active state + restart-count 0 + watchdog

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Fresh systemd install")

Spec Definition-of-done bullet (line 699): "systemd unit deployed; `systemctl status gsfluent-backend` shows active and recent restart count = 0".

- [ ] **Step 1: Read the deploy README for the official install steps**

```bash
cat /home/frankyin/Desktop/work/gsfluent_pkg/deploy/README.md
```

Use the exact commands from `deploy/README.md` (written in Phase 4) as the source of truth. The commands below assume the Phase 4 standard: `systemctl link` + `systemctl enable` + `systemctl start`. If `deploy/README.md` differs, follow the README.

- [ ] **Step 2: Install (or reinstall) the systemd unit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg

# Stop the existing service if any (from earlier tasks).
sudo systemctl stop gsfluent-backend.service 2>/dev/null || true
sudo systemctl disable gsfluent-backend.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/gsfluent-backend.service

# Link the unit file from the repo (so future edits don't require copy).
sudo systemctl link "$(pwd)/deploy/gsfluent-backend.service"
sudo systemctl daemon-reload
sudo systemctl enable gsfluent-backend.service
sudo systemctl start gsfluent-backend.service
```

Expected: each command exits 0. `systemctl enable` should report `Created symlink /etc/systemd/system/multi-user.target.wants/gsfluent-backend.service → /home/.../deploy/gsfluent-backend.service`.

- [ ] **Step 3: Verify status is active**

```bash
sudo systemctl status gsfluent-backend.service --no-pager | head -20
```

Expected: `Active: active (running) since ...`. The "Loaded:" line should point at the linked unit path under the repo.

- [ ] **Step 4: Verify restart count is 0**

```bash
systemctl show gsfluent-backend.service -p NRestarts --value
```

Expected: `0`. If non-zero, the unit started, crashed, restarted at least once — investigate via `journalctl -u gsfluent-backend --since "5 min ago"` before continuing.

- [ ] **Step 5: Verify the watchdog is firing**

The Phase 4 service file should set `WatchdogSec=` (typically 60s) and the backend should `sd_notify("WATCHDOG=1")` periodically. Confirm both:

```bash
# Check the unit file declares a watchdog interval.
grep -E '^WatchdogSec=' /home/frankyin/Desktop/work/gsfluent_pkg/deploy/gsfluent-backend.service

# Check the runtime watchdog is being kept alive (look for sd_notify pings in journal).
sudo journalctl -u gsfluent-backend --since "2 min ago" -o cat \
    | grep -E '"event":' \
    | jq -c 'select(.event == "backend.watchdog.ping" or .event == "watchdog.ping")' \
    | head -3
```

Expected: `WatchdogSec=` line is present (likely `WatchdogSec=60` or similar). At least one watchdog ping event appears in the last 2 minutes (Phase 6 added the ping as a structured event).

If no watchdog ping events appear but `WatchdogSec` is set, the systemd watchdog is still firing — events are an observability nicety, not a contract. The hard contract is: systemd shows `Status: "Running"` and `NRestarts: 0` for at least 2 minutes after `WatchdogSec` elapses.

- [ ] **Step 6: Append the evidence to the verification log**

Append under `### Fresh systemd install`:
- The exact install commands run.
- The `systemctl status` output (top 20 lines).
- The `NRestarts` value.
- The `WatchdogSec` value from the unit file.
- The watchdog ping events (or a note confirming the service stayed `active` past `WatchdogSec * 3`).
- One sentence confirming: "Fresh systemd install: service `active (running)`, `NRestarts=0`, `WatchdogSec=N` configured, watchdog observed firing in journal."

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: manual — fresh systemd install verified (active, NRestarts=0, watchdog firing)"
```

---

### Task 16: Manual verification — cap-violating recipe returns 422, spawns no subprocess

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Cap-violating recipe rejected without subprocess")

This verification was not in the spec's explicit Definition-of-done list but is in the prompt: "Submit a cap-violating recipe via curl; verify 422 with structured error envelope; verify NO subprocess was spawned (check via `ps` or by counting items in `work/_state/runs/`)". It guards Phase 3's promise that recipe caps short-circuit before the GPU sees the recipe.

- [ ] **Step 1: Capture the state-dir baseline**

```bash
PORT=7869
# Adjust the work_dir path to the actual config (read from systemd EnvironmentFile or the unit's Environment= lines).
WORK_DIR=/home/frankyin/Desktop/work/gsfluent_pkg/work
BEFORE=$(ls "$WORK_DIR/_state/runs/" 2>/dev/null | wc -l)
echo "State dir before: $BEFORE runs"

# Also capture: any sim subprocesses currently running?
ps -ef | grep -E 'gs_simulation_building|run_sim.sh' | grep -v grep | tee /tmp/sim-procs-before.txt
```

- [ ] **Step 2: Submit a cap-violating recipe via curl**

A recipe with `particle_count` above the cap. Default cap from Phase 1 `core/limits.py` is `DEFAULT_MAX_PARTICLE_COUNT = 500_000`; pick something like 800_000:

```bash
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "http://127.0.0.1:${PORT}/api/runs" \
    -H "Content-Type: application/json" \
    -d '{
        "recipe_name": "cap_violation_test",
        "recipe_inline": {
            "particle_count": 800000,
            "wall_time_sec": 60
        }
    }')
echo "$RESPONSE"

STATUS=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)
echo "HTTP status: $STATUS"
echo "Body: $BODY" | jq '.'
```

Note: the exact request shape that lets a caller inline a recipe override is owned by Phase 3's `api/runs.py`. If the endpoint doesn't accept `recipe_inline`, swap in whatever Phase 3 exposed for direct-submission tests (commonly a path like `POST /api/runs` with the whole recipe body).

Expected:
- `HTTP status: 422`.
- Body matches spec's error envelope (line 601):
  ```json
  {
    "error": {
      "kind": "cap_exceeded.particle_count",
      "message": "Particle count 800000 exceeds limit 500000",
      "details": { "requested": 800000, "limit": 500000 },
      "trace_id": "..."
    }
  }
  ```

- [ ] **Step 3: Confirm no subprocess was spawned**

```bash
ps -ef | grep -E 'gs_simulation_building|run_sim.sh' | grep -v grep | tee /tmp/sim-procs-after.txt

# Compare:
diff /tmp/sim-procs-before.txt /tmp/sim-procs-after.txt && echo "MATCH: no new sim subprocesses" || echo "DIFFERENCE: a sim subprocess appeared"
```

Expected: `MATCH: no new sim subprocesses`. The two files identical (both empty if no sim was running before, or showing the same PIDs if a long-running sim from Task 13/14 is still alive).

- [ ] **Step 4: Confirm no run-state file was created**

```bash
AFTER=$(ls "$WORK_DIR/_state/runs/" 2>/dev/null | wc -l)
echo "State dir after: $AFTER runs"

if [ "$AFTER" = "$BEFORE" ]; then
    echo "MATCH: state-dir count unchanged ($BEFORE), no run record persisted"
else
    echo "DIFFERENCE: state-dir grew by $((AFTER - BEFORE)) — investigate"
    ls -lt "$WORK_DIR/_state/runs/" | head -5
fi
```

Expected: `MATCH: state-dir count unchanged`. Cap-violating recipes reject at the API boundary (Phase 3's `limits.check_recipe_caps`) — the spec is explicit (line 401): caps run BEFORE `state.create_run_record()`. So no `_state/runs/<id>.json` should land.

- [ ] **Step 5: Confirm the structured error event was emitted**

```bash
sudo journalctl -u gsfluent-backend --since "1 min ago" -o cat \
    | grep -E '"event":' \
    | jq -c 'select(.event | startswith("error.cap_exceeded"))' \
    | head -3
```

Expected: at least one event with `event = error.cap_exceeded.particle_count` (or a closely related dotted name from spec's error taxonomy line 575).

- [ ] **Step 6: Append the evidence to the verification log**

Append under `### Cap-violating recipe rejected without subprocess`:
- The exact curl command + the 422 response body.
- The `ps` diff (empty / no new subprocess).
- The state-dir count diff (unchanged).
- The matching error event from journalctl.
- One sentence confirming: "Cap-violating recipe rejected with 422 + structured error envelope (`kind=cap_exceeded.particle_count`); no subprocess spawned; no run-state file created."

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: manual — cap-violating recipe 422'd at API; no subprocess, no state file"
```

---

### Task 17: Manual verification — streaming cache hit on second load

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "### Streaming cache hit")

Prompt: "Streaming cache hit: load a sequence twice; verify second load skips download (check `cell.cache.hit` event in the log)". This verifies Phase 5's client-side HEAD-skip path.

- [ ] **Step 1: Confirm a `.gsq` cache exists for at least one sequence**

```bash
ls /home/frankyin/Desktop/work/gsfluent_pkg/work/cache/viser/*.gsq 2>/dev/null | head -5
```

Expected: at least one `.gsq` file. If none, pick any sequence with a finished run (`/api/sequences/` should list at least one) and trigger the cache build by hitting `GET /api/sequences/<name>/cache/splats.gsq` once.

Pick one for the verification: assign `SEQ_NAME` to the chosen sequence name.

- [ ] **Step 2: Capture the journalctl baseline**

```bash
SINCE=$(date -Iseconds)
echo "Filter timestamp: $SINCE"

# Optional: clear the client-side cache so the first load is a true fresh download.
# Default client-cache dir from Phase 5: $HOME/.cache/gsfluent or work/cache/viser_client/
# Refer to viser_headless code for the actual path; if unsure, leave the cache and rely on
# `cell.cache.hit` event appearing on BOTH loads (proves the HEAD-skip works either way).
```

- [ ] **Step 3: Issue two loads via the viser_headless control API**

The viser_headless control sidecar runs at `127.0.0.1:8092` (per ARCHITECTURE.md). The `/set` endpoint is how the SPA selects a sequence; Phase 5's HEAD-check path runs on that codepath.

```bash
SEQ_NAME=<chosen-sequence>  # from Step 1

# First load.
curl -s -X POST "http://127.0.0.1:8092/set" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$SEQ_NAME\"}" \
    | jq '.'

sleep 5

# Second load.
curl -s -X POST "http://127.0.0.1:8092/set" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$SEQ_NAME\"}" \
    | jq '.'

sleep 3
```

Expected: each POST returns `{"ok": true, ...}` (or whatever Phase 5 chose). The second load should be visibly faster (sub-second instead of multi-second download).

- [ ] **Step 4: Inspect viser_headless's event log for `cell.cache.hit`**

viser_headless's events go to its own stdout (not the backend's journal). Where they land depends on how viser_headless was launched. Two common places:

```bash
# A: if launched via systemd or supervisor that captures stdout:
sudo journalctl --since "$SINCE" -o cat 2>/dev/null \
    | grep -E '"event":\s*"cell\.cache\.hit"' \
    | head -5

# B: if launched directly from frontend/scripts/start.mjs (most common dev setup):
#    its output goes to wherever the shell that ran `npm start` is logging.
#    Look at the live terminal or check work/logs/.
ls /home/frankyin/Desktop/work/gsfluent_pkg/work/logs/ 2>/dev/null
# If a viser-headless log exists:
grep -E '"event":\s*"cell\.cache\.hit"' /home/frankyin/Desktop/work/gsfluent_pkg/work/logs/viser*.log 2>/dev/null | head -5
```

Expected: at least one event with `event = cell.cache.hit` and `name = <SEQ_NAME>` from the second load (spec line 521-525). If only the second load shows the hit, that confirms the first was a fresh download and the second was a cache hit — exactly what Phase 5 promises.

If the event source isn't easy to find, fall back to a network-level confirmation: while the second load is running, watch backend access logs and confirm no `GET /api/sequences/<SEQ_NAME>/cache/splats.gsq` 200-response appears (only a `HEAD` followed by no subsequent `GET`):

```bash
sudo journalctl -u gsfluent-backend --since "$SINCE" -o cat \
    | grep -E "$SEQ_NAME.*splats\.gsq" \
    | head -10
```

Expected: a `HEAD` line (or an internal Phase 5 `cell.cache.head` event) appears, and no full `GET` follows on the second load.

- [ ] **Step 5: Append the evidence to the verification log**

Append under `### Streaming cache hit`:
- The chosen `SEQ_NAME` and the two control-API POSTs.
- The wall-clock duration difference (eyeball or `time curl ...` — first load multi-second, second load sub-second).
- The `cell.cache.hit` event line (or, if not available, the proof that no full GET followed the second HEAD).
- One sentence confirming: "Second load of sequence X observed `cell.cache.hit` event; no full GET issued; cache HEAD-skip path is live per Phase 5."

- [ ] **Step 6: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: manual — streaming cache hit verified on second load (HEAD-skip working)"
```

---

### Task 18: Resolve open question — `sim.unstable_recipe` classifier

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "## Classifier decision")
- Possibly modify: `server/gsfluent/core/sim_engines/mpm.py` (if classifier missing)
- Possibly create: `server/gsfluent/core/sim_engines/unstable_patterns.yaml` (if implementing)

Spec Open Question 1 (line 775): "**`sim.unstable_recipe` classification** — worth the ~150 lines of stderr pattern matching? Cost/value depends on whether customers write recipes by hand (high value) or via templated UI (low value). Default in spec: include it, parametrize the patterns in a YAML so they can be tuned post-launch."

The plan prompt says: VERIFY in Plan 3 whether the spec's default was implemented; if Plan 3 covered it, this task just confirms; if not, this task is a 1-2 hour follow-up.

- [ ] **Step 1: Audit Phase 3 for the classifier**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
# Check the typed error class exists.
grep -rn "SimUnstableRecipeError" server/gsfluent/ | head -10
# Check the stderr classifier function exists.
grep -rn "unstable_recipe\|classify_stderr\|classify_sim_error" server/gsfluent/core/sim_engines/ | head -10
# Check the YAML pattern file exists.
ls server/gsfluent/core/sim_engines/*.yaml 2>/dev/null
# Check the test exists.
ls server/tests/integration/test_sim_error_classification.py 2>/dev/null
```

Expected (if Phase 3 implemented per spec default):
1. `SimUnstableRecipeError` defined in `server/gsfluent/protocols/sim.py` (added by Plan 1 — verified in Plan 1 Task 7, so this should already exist).
2. A classifier function exists in `server/gsfluent/core/sim_engines/mpm.py` (or a sibling `classify.py`) that maps known stderr patterns (CFL violation, "illegal memory access", etc.) to `SimUnstableRecipeError` and other typed errors.
3. A YAML file at `server/gsfluent/core/sim_engines/unstable_patterns.yaml` (or similar) lists the patterns.
4. `tests/integration/test_sim_error_classification.py` exists and parametrizes over the YAML.

- [ ] **Step 2: Choose the branch based on the audit**

**Branch A: Classifier landed as spec default (~5 minutes confirm)**

If all four items from Step 1 are present, this task is a confirmation only.

Confirm the YAML has at least the canonical CFL-violation pattern:

```bash
cat server/gsfluent/core/sim_engines/unstable_patterns.yaml 2>/dev/null
```

Expected: at least one entry tagged `kind: sim.unstable_recipe` matching a CFL- or illegal-memory-style stderr pattern.

Confirm the test runs green for the unstable-recipe case (already verified in Task 4 but re-state):

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/integration/test_sim_error_classification.py -v -k "unstable_recipe" 2>&1 | tail -10
```

Expected: at least one test parametrized as `unstable_recipe` passes.

Append to the verification log under `## Classifier decision`:
```
Spec Open Question 1 outcome: IMPLEMENTED PER DEFAULT (verified in Phase 3).
- Typed error: server/gsfluent/protocols/sim.py: SimUnstableRecipeError
- Classifier: server/gsfluent/core/sim_engines/mpm.py:_classify_stderr (or similar)
- YAML: server/gsfluent/core/sim_engines/unstable_patterns.yaml (N patterns)
- Test: tests/integration/test_sim_error_classification.py (M parametrized cases, all green)

No follow-up work needed. Patterns can be tuned post-launch by editing the YAML.
```

**Branch B: Classifier missing (1-2 hour follow-up)**

If any of the four items from Step 1 is missing, implement the spec default. This is in-scope for Phase 7 because the spec promised to decide here.

Minimal scope (sticking to spec line 775's "include it, parametrize the patterns in a YAML"):

1. **YAML pattern file** — `server/gsfluent/core/sim_engines/unstable_patterns.yaml`:

```yaml
# Stderr patterns that classify a sim crash as sim.unstable_recipe
# (numerical instability) vs sim.crashed (unknown). Tunable post-launch.
patterns:
  - kind: sim.unstable_recipe
    pattern: "CFL violation"
    hint: "Numerical instability (CFL); try increasing substep_dt"
  - kind: sim.unstable_recipe
    pattern: "illegal memory access"
    hint: "Likely GPU access fault from runaway particle; check recipe boundaries"
  - kind: sim.unstable_recipe
    pattern: "nan in position"
    hint: "NaN particle positions; check material params"
  - kind: sim.gpu_oom
    pattern: "out of memory"
    hint: "GPU OOM; lower particle_count or substep_dt"
  - kind: sim.gpu_oom
    pattern: "CUDA out of memory"
    hint: "GPU OOM (PyTorch); lower particle_count or substep_dt"
```

2. **Classifier function in `core/sim_engines/mpm.py`** (or a sibling `classify.py` if the file is large) — read the YAML once at module import, scan the last 32 KB of stderr on sim failure, return the first matching `kind` (or `sim.crashed` if no match), and raise the corresponding typed error from `protocols/sim.py`.

3. **Integration test `tests/integration/test_sim_error_classification.py`** — parametrize over YAML entries; use the `mock_sim.sh` fixture's `MOCK_SIM_STDERR_PATTERN` knob to inject each pattern; assert the corresponding typed error is raised by the orchestrator.

4. **Commit**:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add server/gsfluent/core/sim_engines/unstable_patterns.yaml \
        server/gsfluent/core/sim_engines/mpm.py \
        server/tests/integration/test_sim_error_classification.py
git commit -m "phase-7: classifier — sim stderr → typed errors via YAML patterns (spec OQ 1)"
```

Append to the verification log under `## Classifier decision`:
```
Spec Open Question 1 outcome: IMPLEMENTED IN PHASE 7 (Plan 3 omitted it).
- Added: server/gsfluent/core/sim_engines/unstable_patterns.yaml (N patterns)
- Added: classifier function in core/sim_engines/mpm.py
- Added: tests/integration/test_sim_error_classification.py (M parametrized cases)

Re-ran integration test category (Task 4 floor +M). Patterns can be tuned
post-launch by editing the YAML.
```

- [ ] **Step 3: Re-run the integration suite if Branch B was taken**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/integration/ -v --tb=short 2>&1 | tail -20
```

Update the Task 4 verification-log entry with the new count if it changed.

- [ ] **Step 4: Commit the verification log**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: open question 1 resolved — sim.unstable_recipe classifier (Branch A or B)"
```

---

### Task 19: Update README.md for the new component layout, systemd, and cap-config env vars

**Files:**
- Modify: `README.md` (Chinese, primary)
- Modify: `README.en.md` (English mirror)

The current `README.md` (147 lines) and `README.en.md` (152 lines) both describe the pre-bulletproofing world: `supervise.sh` for process management, no cap configuration, no mention of the six-Protocol layout. Spec Definition-of-done bullet (line 700): "README / deploy docs updated for systemd install".

- [ ] **Step 1: Audit the current README.md sections to target**

Three sections need surgical edits:
1. **"服务端运维" (line 81-103)** — currently describes `supervise.sh`; must point at systemd + `deploy/README.md`.
2. **"仓库结构" (line 107-121)** — table mentions `server/supervise.sh`; must add `deploy/`, remove the `supervise.sh` row.
3. **NEW SECTION between "服务端运维" and "仓库结构"** — add a "限额配置" (cap config) section listing the cap env vars.

Sections to leave alone:
- "快速上手(团队成员)" (frontend dev workflow — unchanged).
- "架构" diagram (still accurate at the diagram level).
- "API 参考" (still accurate — endpoint list unchanged).
- "故障排查" (mostly still accurate; one row references `supervise.sh status` and gets updated).

- [ ] **Step 2: Replace the "服务端运维" section in README.md**

Use `Edit` to replace the entire block from `## 服务端运维` through (but not including) `## 仓库结构`. Replacement text:

```markdown
## 服务端运维

后端进程由 systemd 管理。安装步骤、journalctl 配方、健康检查端点详见
[`deploy/README.md`](deploy/README.md)。最常用的几个命令:

```bash
# 安装 unit(首次)
sudo systemctl link "$(pwd)/deploy/gsfluent-backend.service"
sudo systemctl enable --now gsfluent-backend.service

# 状态 / 重启 / 日志
sudo systemctl status gsfluent-backend.service
sudo systemctl restart gsfluent-backend.service
sudo journalctl -u gsfluent-backend.service -f -o cat
```

systemd 负责重启、看门狗、PID 跟踪。`server/supervise.sh` 已删除。

端口绑定:

| 进程        | 监听              | 公网映射                  |
|-------------|-------------------|---------------------------|
| v1 backend  | `0.0.0.0:7869`    | `your-backend:port` (NAT) |

仿真后处理(把 ply 转 gsq 缓存、打包 frames.bin)在 `server/tools/` 下,
按需 ssh 进服务器手动跑。

日志走 journald,JSON 一行一条事件:

```bash
sudo journalctl -u gsfluent-backend.service -o cat | jq -c 'select(.event)'
```

Python 解释器在 `.env` 里通过 `GSFLUENT_API_PYTHON` / `GSFLUENT_SIM_PYTHON`
配,需要改就改 `.env`。

---

## 限额配置(防止跑飞)

后端在 API 边界对 recipe 做 cap 校验,违规直接 422 拒绝,不会拉起子进程。
默认值在 `server/gsfluent/core/limits.py:DEFAULT_*`,可通过环境变量改写:

| 环境变量                          | 默认值      | 含义                                 |
|-----------------------------------|-------------|--------------------------------------|
| `GSFLUENT_MAX_PARTICLE_COUNT`     | `500000`    | recipe 单次允许的最大粒子数          |
| `GSFLUENT_MAX_WALL_TIME_SEC`      | `3600`      | sim 最长 wall-time 秒数(超时 PG-kill)|
| `GSFLUENT_MAX_RECIPE_BYTES`       | `16384`     | recipe JSON 体积上限(防 DoS)       |

cap 触发返回的错误结构(spec 第 599 行):

```json
{
  "error": {
    "kind": "cap_exceeded.particle_count",
    "message": "Particle count 800000 exceeds limit 500000",
    "details": { "requested": 800000, "limit": 500000 },
    "trace_id": "01H8K2P..."
  }
}
```

---

## 组件分层

后端按六层 Protocol 切分,每层一个 `typing.Protocol` 接口 + 一个
当前的具体实现,在 `server/gsfluent/composition.py` 一次性接装:

| 层 | Protocol                                    | 当前实现                                   |
|----|---------------------------------------------|--------------------------------------------|
| L0 | (HTTP)                                      | `server/gsfluent/api/*.py`                 |
| L1 | `protocols/runs.py:RunManager`              | `core/run_manager.py:AsyncioRunManager`    |
| L2 | `protocols/sim.py:SimulationEngine`         | `core/sim_engines/mpm.py:MPMSimulationEngine` |
| L3 | `protocols/fuse.py:Fuser`                   | `core/fusers/knn_kabsch.py:KNNKabschFuser` |
| L4 | `protocols/cache.py:CacheCodec`             | `core/codecs/gsq.py:GSQCodec`              |
| L5 | `protocols/storage.py:Storage`              | `storage/filesystem.py:FilesystemStorage`  |
| L6 | `protocols/observability.py:EventEmitter`   | `observability/jsonlog.py:StdlibJSONEmitter` |

每个 Protocol 有一套 conformance 测试(`server/tests/protocols/test_*_conformance.py`),
任何新实现替换进来时跑一次就能确认契约。详细架构说明见
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

```

- [ ] **Step 3: Update the "仓库结构" table in README.md**

Use `Edit` to replace the `server/` row and add a `deploy/` row.

old_string (the row with `server/`):
```
| `server/`           | FastAPI v1 backend,只在服务器跑。REST 路由 + runner 在 `gsfluent/`。 |
| `server/tools/`     | 仿真包装(`run_sim.sh`)、PLY → gsq 转换(`pack_splats.py`)、fuse、迁移等服务端脚本。   |
```

new_string:
```
| `server/`           | FastAPI v1 backend,只在服务器跑。六层 Protocol + composition root 在 `gsfluent/`。 |
| `server/tools/`     | 仿真包装薄壳(`run_sim.sh` 现在 ≈20 行 conda-activate),其余 PLY/打包脚本现在是 `core/` 实现的 CLI 包装。 |
| `deploy/`           | systemd unit (`gsfluent-backend.service`) 和部署手册(`README.md`)。 |
```

- [ ] **Step 4: Update the "故障排查" table in README.md**

Find the row referencing `supervise.sh status` and update it.

old_string:
```
| `/api/*` 全部 502 / connection refused | 服务器后端挂了,在服务器跑 `bash server/supervise.sh status`            |
```

new_string:
```
| `/api/*` 全部 502 / connection refused | 服务器后端挂了,跑 `sudo systemctl status gsfluent-backend.service`     |
```

- [ ] **Step 5: Mirror all changes into README.en.md**

Apply the same three edits (server-ops section rewrite, repo-layout table update, troubleshooting row) to `README.en.md`, translated to English. Use the same systemd commands, the same cap-config env-var table (English column headers), and the same six-layer table.

For the server-ops rewrite in `README.en.md`, replace the block from `## Server admin` through (but not including) `## Repo layout` with:

```markdown
## Server admin

The backend is supervised by systemd. Install steps, journalctl recipes,
and the health-check endpoint are in [`deploy/README.md`](deploy/README.md).
Common commands:

```bash
# First-time install
sudo systemctl link "$(pwd)/deploy/gsfluent-backend.service"
sudo systemctl enable --now gsfluent-backend.service

# Status / restart / live logs
sudo systemctl status gsfluent-backend.service
sudo systemctl restart gsfluent-backend.service
sudo journalctl -u gsfluent-backend.service -f -o cat
```

systemd handles restarts, the watchdog, and PID tracking.
`server/supervise.sh` has been removed.

Bindings:

| Process     | Listens on        | Public mapping              |
|-------------|-------------------|-----------------------------|
| v1 backend  | `0.0.0.0:7869`    | `your-backend:port` (NAT)   |

Post-sim utilities (PLY → gsq cache, frames.bin packing) live in
`server/tools/` and run on the server via ssh as needed.

Logs go to journald as one JSON event per line:

```bash
sudo journalctl -u gsfluent-backend.service -o cat | jq -c 'select(.event)'
```

Python interpreters are configured via `.env`
(`GSFLUENT_API_PYTHON`, `GSFLUENT_SIM_PYTHON`).

---

## Cap configuration (runaway-recipe defence)

The backend validates every incoming recipe against caps at the API
boundary. Violations return 422 without spawning any subprocess. Defaults
are in `server/gsfluent/core/limits.py:DEFAULT_*`; override via env vars:

| Env var                           | Default     | Meaning                                      |
|-----------------------------------|-------------|----------------------------------------------|
| `GSFLUENT_MAX_PARTICLE_COUNT`     | `500000`    | Max particles per submitted recipe           |
| `GSFLUENT_MAX_WALL_TIME_SEC`      | `3600`      | Max sim wall-time (PG-killed on overrun)     |
| `GSFLUENT_MAX_RECIPE_BYTES`       | `16384`     | Max recipe JSON size (DoS guard)             |

A cap-violation response (spec line 599):

```json
{
  "error": {
    "kind": "cap_exceeded.particle_count",
    "message": "Particle count 800000 exceeds limit 500000",
    "details": { "requested": 800000, "limit": 500000 },
    "trace_id": "01H8K2P..."
  }
}
```

---

## Component layout

The backend is split into six layers, each a `typing.Protocol` interface
plus a current concrete implementation, wired in
`server/gsfluent/composition.py`:

| Layer | Protocol                                    | Current impl                               |
|-------|---------------------------------------------|--------------------------------------------|
| L0    | (HTTP)                                      | `server/gsfluent/api/*.py`                 |
| L1    | `protocols/runs.py:RunManager`              | `core/run_manager.py:AsyncioRunManager`    |
| L2    | `protocols/sim.py:SimulationEngine`         | `core/sim_engines/mpm.py:MPMSimulationEngine` |
| L3    | `protocols/fuse.py:Fuser`                   | `core/fusers/knn_kabsch.py:KNNKabschFuser` |
| L4    | `protocols/cache.py:CacheCodec`             | `core/codecs/gsq.py:GSQCodec`              |
| L5    | `protocols/storage.py:Storage`              | `storage/filesystem.py:FilesystemStorage`  |
| L6    | `protocols/observability.py:EventEmitter`   | `observability/jsonlog.py:StdlibJSONEmitter` |

Every Protocol has a conformance suite
(`server/tests/protocols/test_*_conformance.py`); swapping an
implementation only requires re-running the suite against the new impl.
Architecture details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

```

Same as `README.md`, update the "Repo layout" table to swap `supervise.sh` mentions and add a `deploy/` row, and update the troubleshooting row about `supervise.sh status` to use `systemctl status gsfluent-backend.service`.

old_string (repo-layout):
```
| `server/`             | FastAPI v1 backend. REST routes + runner live under `gsfluent/`.        |
| `server/tools/`       | Sim wrapper (`run_sim.sh`), PLY → gsq converter (`pack_splats.py`), fuse, migration.      |
```

new_string:
```
| `server/`             | FastAPI v1 backend. Six-Protocol layout + composition root under `gsfluent/`. |
| `server/tools/`       | Sim wrapper (`run_sim.sh`, now a ~20-line conda-activate shim) and CLI wrappers around `core/` impls. |
| `deploy/`             | systemd unit (`gsfluent-backend.service`) and deploy guide (`README.md`).     |
```

old_string (troubleshooting):
```
| All `/api/*` calls 502 / refused       | Server backend is down — on the server run `bash server/supervise.sh status` |
```

new_string:
```
| All `/api/*` calls 502 / refused       | Server backend is down — run `sudo systemctl status gsfluent-backend.service` |
```

- [ ] **Step 6: Commit both README files together**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add README.md README.en.md
git commit -m "phase-7: README — systemd install, cap config, six-Protocol layout (zh + en)"
```

---

### Task 20: Update docs/ARCHITECTURE.md for the new component layout

**Files:**
- Modify: `docs/ARCHITECTURE.md`

The current `docs/ARCHITECTURE.md` (330 lines) accurately describes the data pipeline (Z-up invariant, .gsq format, runtime topology) but is silent about the bulletproofing slice's six-Protocol layout, structured events, and systemd supervision. Spec Definition-of-done bullet (line 700) and prompt: "Update `README.md` and `docs/ARCHITECTURE.md` to reflect: new component layout (six Protocols + composition root + structured events), systemd deployment instructions (pointer to `deploy/README.md`), cap configuration env vars".

- [ ] **Step 1: Audit ARCHITECTURE.md sections to target**

Untouched (still accurate):
- "What the system is" (lines 9-46) — pipeline diagram still right.
- "Data contracts" (lines 150-246) — Z-up, _meta.json, .gsq format unchanged.
- "Runtime topology" (lines 249-289) — diagram still accurate.
- "Where to put new things" (lines 293-303) — still accurate; minor edit on the "new backend endpoint" row.
- "What's next" (lines 306-320) — research roadmap, unchanged.
- "What this is NOT" (lines 323-330) — unchanged.

Surgical edits:
- "Components and responsibilities" (lines 50-135) — three subsections need rewrites to describe the new layout.
- A NEW section "Backend internals — six-Protocol layout" inserted before or replacing the `server/gsfluent/` subsection.
- The `server/supervise.sh` mention (line 67-68, 90-95) — replaced with systemd pointer to `deploy/README.md`.

- [ ] **Step 2: Rewrite the `server/gsfluent/` subsection**

Use `Edit` to replace the entire block from `### \`server/gsfluent/\` — v1 backend` through (but not including) `### \`server/tools/\` — server-side pipeline glue`.

old_string:
```
### `server/gsfluent/` — v1 backend

- FastAPI process on `0.0.0.0:7869`, reached publicly as
  `your-backend:port` via NAT. The wire contract for every route is in
  [`docs/API.md`](API.md) / [`docs/API.zh.md`](API.zh.md).
- Mounts REST routes under `/api/*`: `recipes`, `models`, `runs`,
  `sequences`, `schemas`, plus the per-frame xyz WebSocket at
  `/api/stream`. The SPA static fallback is mounted last so `/api/*`
  always wins on prefix conflict.
- **Owns**: the library API surface, the WS frame pump, the runner that
  spawns sim subprocesses.
- **Does NOT own**: viewer rendering (client-side), per-cell viser
  caches (built by `server/tools/pack_splats.py`, served by
  `frontend/python/viser_headless.py`).
- Process management: `server/supervise.sh up|stop|status` — a small
  shell supervisor (no systemd, no docker) that respawns the backend
  if it dies.
```

new_string:
```
### `server/gsfluent/` — v1 backend

- FastAPI process on `0.0.0.0:7869`, reached publicly as
  `your-backend:port` via NAT. The wire contract for every route is in
  [`docs/API.md`](API.md) / [`docs/API.zh.md`](API.zh.md).
- Mounts REST routes under `/api/*`: `recipes`, `models`, `runs`,
  `sequences`, `schemas`, plus the per-frame xyz WebSocket at
  `/api/stream`. The SPA static fallback is mounted last so `/api/*`
  always wins on prefix conflict.
- **Owns**: the library API surface, the WS frame pump, the run lifecycle
  controller (Layer 1, `RunManager`), and the sim orchestrator
  (Layer 2, `SimulationEngine`).
- **Does NOT own**: viewer rendering (client-side), per-cell viser
  caches (built by `core/codecs/gsq.py:GSQCodec`, served by
  `frontend/python/viser_headless.py`).
- Process management: **systemd**. The unit file lives at
  `deploy/gsfluent-backend.service` and is installed via
  `systemctl link` per [`deploy/README.md`](../deploy/README.md). The
  watchdog (`WatchdogSec=...`) is kept alive by an `sd_notify` ping
  from the backend; failures trigger systemd restart. The previous
  `server/supervise.sh` shell supervisor has been removed.

#### Six-Protocol layout (post-bulletproofing slice)

The backend is split into six layers, each a `typing.Protocol` interface
with a current concrete implementation. All wiring happens in
`server/gsfluent/composition.py:build_app(AppConfig)`.

| Layer | Protocol (`gsfluent/protocols/`)            | Current impl                                       |
|-------|---------------------------------------------|----------------------------------------------------|
| L1    | `runs.py:RunManager`                        | `core/run_manager.py:AsyncioRunManager`            |
| L2    | `sim.py:SimulationEngine`                   | `core/sim_engines/mpm.py:MPMSimulationEngine`      |
| L3    | `fuse.py:Fuser`                             | `core/fusers/knn_kabsch.py:KNNKabschFuser`         |
| L4    | `cache.py:CacheCodec`                       | `core/codecs/gsq.py:GSQCodec`                      |
| L5    | `storage.py:Storage`                        | `storage/filesystem.py:FilesystemStorage`          |
| L6    | `observability.py:EventEmitter`             | `observability/jsonlog.py:StdlibJSONEmitter`       |

Each Protocol has a conformance suite at
`server/tests/protocols/test_*_conformance.py`. Swapping an
implementation (e.g. `SPZCodec` for `GSQCodec`, `S3Storage` for
`FilesystemStorage`, `CeleryRunManager` for `AsyncioRunManager`) is a
single-class change in `composition.py` after the new impl passes the
conformance suite.

#### Hardening threads

1. **Process model.** Sim subprocesses spawn in a fresh process group
   (`asyncio.create_subprocess_*(start_new_session=True)`). Cancellation
   and wall-time timeout escalate via `os.killpg(pgid, SIGTERM)` →
   30s grace → `os.killpg(pgid, SIGKILL)`.
2. **Recipe boundary.** `POST /api/runs` strict-Pydantic-validates and
   runs `core/limits.check_recipe_caps()` before any state file is
   written or any subprocess is spawned. Violations return HTTP 422
   with a structured error envelope; see the cap-config table in
   [`README.md`](../README.md) for env-var overrides.
3. **HTTP cache hygiene.** `api/sequences.py` returns `.gsq` with
   `Cache-Control: public, immutable, max-age=31536000` and
   `ETag: "<size>-<mtime>"`. `If-None-Match` matching returns 304.
   Client `viser_headless._sync_cell_gsq_streaming` HEAD-checks before
   download (cache hit → skip) and uses `Range: bytes=<n>-` to resume
   from `.partial` (cache miss with prior interrupted download).
4. **Structured observability.** Every state transition emits one
   structured JSON event through `EventEmitter`. Events have the shape
   `{"ts": "<ISO-8601>", "level": "INFO", "event": "<dotted.noun.verb>",
    "run_id": "...", ...}`. The lifecycle event chain is:
   `run.queued` → `run.started` → `run.preflight_ok` → `sim.started` →
   `sim.completed` → `run.simmed` → `run.fused` → `run.packed` →
   `run.completed`. Cancellation adds `run.cancelling` → `run.cancelled`;
   failure swaps the terminal event for `run.failed` with an
   `error.<kind>` companion. Boot recovery emits `boot.run.reattached`
   or `boot.run.interrupted` per recovered record.

#### Run state persistence

Every run owns a JSON file at `work/_state/runs/<run_id>.json`. Writes
are atomic (temp file + rename). On startup, `RunManager.recover_on_boot()`
scans the directory, cross-checks PID + `/proc/<pid>/stat` start-time
against persisted records, and reconciles:

- Live PID + matching starttime → re-attach.
- Dead PID or starttime mismatch → mark `interrupted` with
  `error.kind = internal.backend_restarted`.
- Already terminal → no-op.

Runs are never auto-resumed; customers re-submit interrupted recipes.

```

- [ ] **Step 3: Rewrite the `server/recipes/, server/patches/, server/supervise.sh` subsection**

The `supervise.sh` mention here is now obsolete. Use `Edit` to replace.

old_string:
```
### `server/recipes/`, `server/patches/`, `server/supervise.sh`

- `recipes/*.json` — physics recipes consumed by the server-side sim.
- `patches/gs_simulation_building.patched.py` — patched copy of the
  upstream GaussianFluent sim file.
- `supervise.sh` — backend process manager described above.
```

new_string:
```
### `server/recipes/`, `server/patches/`, `deploy/`

- `recipes/*.json` — physics recipes consumed by the server-side sim.
- `patches/gs_simulation_building.patched.py` — patched copy of the
  upstream GaussianFluent sim file.
- `deploy/gsfluent-backend.service` — systemd unit for the backend.
- `deploy/README.md` — install steps, journalctl recipes, healthcheck
  configuration.
```

- [ ] **Step 4: Update the `server/tools/` subsection to reflect the script-→-CLI-wrapper refactor**

Phase 2 moved `pack_splats.py`, `fuse_to_full_ply.py`, and the orchestration body of `run_sim.sh` into the `core/` package. The script files still exist as thin CLI wrappers. Reflect this:

old_string:
```
### `server/tools/` — server-side pipeline glue

- `fuse_to_full_ply.py` — sim_*.ply + reference 3DGS → frame_*.ply.
  K-NN skinning and per-frame Kabsch rotation behind flags.
- `pack_splats.py` — frame_*.ply → `splats.gsq` (visual-lossless
  streamable splat cache: int16-quantized xyz + axis-vec quats per
  frame, fp16 rgb/scales + uint8 opacity static, zstd-compressed
  per-frame chunks. ~3× smaller than the retired `.npz` format.
  Format spec in the pack_splats.py docstring). Served on demand
  via `/api/sequences/{name}/cache/splats.gsq` and streamed by
  viser_headless's `/sync_cell`.
- `pack_sim_splats.py` — same encode but reads raw `sim_*.ply` instead
  of fused frame plys. Produces a "no-fuse" A/B sequence for
  comparison. Run manually; not part of the default build flow.
- `pack_sequence.py` — frame_*.ply → `frames.bin` (GSSQ int16-quantized
  xyz). Used by Points mode (`gsfluent/core/frame_stream.py:PackedReader`,
  WS stream). ~30× smaller on disk than per-frame plies.
- `run_sim.sh` — sim launcher invoked by the v1 backend's runner.
- `migrate_to_library.py`, `check_recipe_compat.py` — one-shot utilities.
```

new_string:
```
### `server/tools/` — server-side pipeline glue (thin CLI wrappers)

After the bulletproofing slice, the logic in these scripts lives in
`server/gsfluent/core/`; the scripts here are thin CLI wrappers kept
for ssh-driven one-shot runs.

- `fuse_to_full_ply.py` — CLI wrapper around
  `core/fusers/knn_kabsch.py:KNNKabschFuser`. K-NN skinning and
  per-frame Kabsch rotation.
- `pack_splats.py` — CLI wrapper around
  `core/codecs/gsq.py:GSQCodec`. Encodes frame_*.ply →
  `splats.gsq` (visual-lossless streamable splat cache:
  int16-quantized xyz + axis-vec quats per frame, fp16 rgb/scales +
  uint8 opacity static, zstd-compressed per-frame chunks. ~3× smaller
  than the retired `.npz` format. Format spec in the
  pack_splats.py docstring). Served on demand via
  `/api/sequences/{name}/cache/splats.gsq` and streamed by
  viser_headless's `/sync_cell`.
- `pack_sim_splats.py` — same encode but reads raw `sim_*.ply` instead
  of fused frame plys. Produces a "no-fuse" A/B sequence for
  comparison. Run manually; not part of the default build flow.
- `pack_sequence.py` — frame_*.ply → `frames.bin` (GSSQ int16-quantized
  xyz). Used by Points mode (`gsfluent/core/frame_stream.py:PackedReader`,
  WS stream). ~30× smaller on disk than per-frame plies.
- `run_sim.sh` — ~20-line conda-activate shim that execs the sim entry
  point in `core/sim_engines/mpm.py`. The sim-orchestration logic
  (PG-spawn, wall-time enforcement, error classification) lives in the
  backend, not in this script.
- `migrate_to_library.py`, `check_recipe_compat.py` — one-shot utilities.
```

- [ ] **Step 5: Update the "Where to put new things" table**

Add a row for "a new sim engine" and update the backend-endpoint row to mention the composition root.

old_string:
```
| Adding... | Goes in... |
|---|---|
| A new sim recipe | `server/recipes/<name>.json`; consumed server-side |
| A new fuse strategy (K-NN variant, MLS, ...) | `server/tools/fuse_to_full_ply.py` as a flag, OR a sibling `server/tools/fuse_<name>.py` |
| A viewer-specific transform | The viewer's wrapper (`vkgs_play.py` for vkgs; `viser_headless.py` for splat). NEVER mutate library frames. |
| A new backend endpoint | `server/gsfluent/api/<route>.py` |
| A web-side renderer mode | `frontend/src/components/viewport/<NewMode>.tsx` |
| A one-shot migration | `server/tools/_oneshot/<date>_<purpose>.py`, not flat in `server/tools/` |
```

new_string:
```
| Adding... | Goes in... |
|---|---|
| A new sim recipe | `server/recipes/<name>.json`; consumed server-side |
| A new fuse strategy (K-NN variant, MLS, ...) | A new class implementing `protocols/fuse.py:Fuser` under `core/fusers/<name>.py`; wire in `composition.py` |
| A new sim engine (alternative physics, mock) | A new class implementing `protocols/sim.py:SimulationEngine` under `core/sim_engines/<name>.py`; wire in `composition.py` |
| A new cache codec (SPZ, 4DGS, ...) | A new class implementing `protocols/cache.py:CacheCodec` under `core/codecs/<name>.py`; wire in `composition.py` |
| A new storage backend (S3, GCS, ...) | A new class implementing `protocols/storage.py:Storage` under `storage/<name>.py`; wire in `composition.py` |
| A viewer-specific transform | The viewer's wrapper (`vkgs_play.py` for vkgs; `viser_headless.py` for splat). NEVER mutate library frames. |
| A new backend endpoint | `server/gsfluent/api/<route>.py`; mount in `composition.py:build_app` |
| A web-side renderer mode | `frontend/src/components/viewport/<NewMode>.tsx` |
| A one-shot migration | `server/tools/_oneshot/<date>_<purpose>.py`, not flat in `server/tools/` |
```

- [ ] **Step 6: Update the status header at the top of the file**

old_string:
```
Status: 2026-05-20. Describes the system as deployed today: a single v1
backend on your server, a client-local SPA + viser pair on each teammate's
machine, and a public NAT port linking the two.
```

new_string:
```
Status: 2026-05-22 (post backend-bulletproofing slice). Describes the
system as deployed today: a single v1 backend on your server (split
into six Protocols + composition root, supervised by systemd), a
client-local SPA + viser pair on each teammate's machine (with
HEAD-skip + Range-resume on the streaming cache), and a public NAT port
linking the two.
```

- [ ] **Step 7: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/ARCHITECTURE.md
git commit -m "phase-7: ARCHITECTURE — six-Protocol layout, systemd, structured events, hardening threads"
```

---

### Task 21: Create CHANGELOG.md with the bulletproofing slice's user-visible changes

**Files:**
- Create: `CHANGELOG.md`

Prompt: "Update `CHANGELOG.md` if it exists (check first); if not, create one with the bulletproofing slice's user-visible changes." Audit at planning time confirmed no CHANGELOG.md exists; this task creates one from scratch using Keep-a-Changelog format.

- [ ] **Step 1: Create CHANGELOG.md from scratch**

Write `CHANGELOG.md` with this content:

```markdown
# Changelog

All notable user-visible changes to gsfluent. Follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Backend bulletproofing slice

Customer-facing hardening sprint. Pipeline shape (recipe → sim → fuse →
pack → cache → stream → render) is unchanged. Six new Protocols + a
composition root sit behind the API. systemd replaces the previous
shell supervisor. Streaming cache becomes ETag-honest.

### Added

- **Six-Protocol component layout.** `RunManager`, `SimulationEngine`,
  `Fuser`, `CacheCodec`, `Storage`, `EventEmitter` Protocols under
  `server/gsfluent/protocols/`. Concrete impls wired in
  `server/gsfluent/composition.py`. Each Protocol has a conformance
  test suite under `server/tests/protocols/test_*_conformance.py`.
- **Recipe caps.** Three env-var-configurable caps applied at the API
  boundary before any subprocess or state file:
  - `GSFLUENT_MAX_PARTICLE_COUNT` (default `500000`)
  - `GSFLUENT_MAX_WALL_TIME_SEC` (default `3600`)
  - `GSFLUENT_MAX_RECIPE_BYTES` (default `16384`)
  Violations return HTTP 422 with a structured error envelope including
  `trace_id`.
- **Structured JSON events.** Every state transition emits one JSON
  event through `EventEmitter` (`StdlibJSONEmitter` writes to stdout →
  journald). Per-run events auto-attach `run_id` and `sequence_name`
  via `EventEmitter.child(...)`. Lifecycle chain:
  `run.queued` → `run.started` → `run.preflight_ok` → `sim.started` →
  `sim.completed` → `run.simmed` → `run.fused` → `run.packed` →
  `run.completed`.
- **Cancellation that actually cancels.** Sim subprocesses spawn in a
  fresh process group; `POST /api/runs/<id>/cancel` sends
  `SIGTERM` to the entire PG and escalates to `SIGKILL` after 30s.
- **Wall-time enforcement.** Sim runs are wrapped in
  `asyncio.wait_for(..., timeout=wall_time_cap)`; overruns trigger the
  same PG-kill escalation.
- **Run-state persistence + boot recovery.** Every run owns
  `work/_state/runs/<id>.json` written atomically (temp file + rename).
  On startup, `RunManager.recover_on_boot()` reconciles with live PIDs
  (cross-checked against `/proc/<pid>/stat` start-time to avoid PID
  reuse). In-flight runs without a live PID are marked `interrupted`
  with `error.kind = internal.backend_restarted`; runs are never
  auto-resumed.
- **systemd supervision.** `deploy/gsfluent-backend.service` +
  `deploy/README.md`. The unit declares `WatchdogSec=...`; the backend
  pings via `sd_notify("WATCHDOG=1")` from an async heartbeat.
- **Streaming cache that respects ETags.**
  - Server: `api/sequences.py` returns `.gsq` with
    `Cache-Control: public, immutable, max-age=31536000` and
    `ETag: "<size>-<mtime>"`. `If-None-Match` matching returns 304.
  - Client: `viser_headless._sync_cell_gsq_streaming` HEAD-checks
    before download (cache hit → skip) and resumes with `Range: bytes=<n>-`
    from `.partial` (cache miss with prior interrupted download).
    Emits `cell.cache.hit` and `cell.cache.resumed` events.
- **Real health signals.** `GET /api/health` now returns GPU
  reachability, `sim_home` existence, disk-free percentage, and last
  successful run timestamp.
- **Typed error taxonomy.** All sim/fuse/codec/storage errors carry a
  dotted `kind` and a `trace_id`. See the table in
  `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md`
  Section "Error handling".

### Changed

- **`server/tools/run_sim.sh`** slimmed from 197 lines to ~20-line
  conda-activate shim. Sim orchestration (PG-spawn, wall-time
  enforcement, error classification) moved into
  `core/sim_engines/mpm.py`.
- **`server/tools/fuse_to_full_ply.py`** is now a CLI wrapper around
  `core/fusers/knn_kabsch.py:KNNKabschFuser`. Behavior unchanged for
  ssh-driven one-shot runs.
- **`server/tools/pack_splats.py`** is now a CLI wrapper around
  `core/codecs/gsq.py:GSQCodec`. `.gsq` wire format unchanged.
- **`server/gsfluent/core/runner.py`** renamed to
  `core/run_manager.py`; implements `AsyncioRunManager` conforming to
  `protocols.RunManager`. Behavior preserved across the refactor; new
  behavior gated by the additions above.
- **`server/gsfluent/core/library.py`** filesystem operations extracted
  to `storage/filesystem.py:FilesystemStorage` conforming to
  `protocols.Storage`. Library business logic stays in `library.py`.
- **`api/runs.py`** moved from permissive validation to strict
  Pydantic + cap-check before persistence.
- **`api/sequences.py`** now uses `Storage.stat()` + `Storage.get_range()`
  instead of direct filesystem calls.
- **Client viser_headless rename:** `npz_root` → `cache_root` and
  `--npz_dir` → `--cache-dir` for clarity. The old name is accepted as
  a deprecated alias for one release.
- **Env-var rename:** `GSFLUENT_NPZ_REBUILD` → `GSFLUENT_CACHE_REBUILD`.
  The old name is accepted as a deprecated alias for one release.

### Removed

- **`server/supervise.sh`** (83-line shell supervisor) — replaced by
  systemd. See [`deploy/README.md`](deploy/README.md) for the install
  steps.

### Fixed

- Cancellation that previously left zombie sim processes now reliably
  kills the entire process group, including child taichi/warp workers.
- Backend restarts that previously left runs stuck in `running` state
  now mark them `interrupted` so the API surface reflects reality.

### Deprecated

- `--npz_dir` / `npz_root` (viser_headless): use `--cache-dir` /
  `cache_root`. One-release transition window.
- `GSFLUENT_NPZ_REBUILD`: use `GSFLUENT_CACHE_REBUILD`. One-release
  transition window.

### Security

- Recipe boundary now rejects oversized payloads (DoS guard via
  `GSFLUENT_MAX_RECIPE_BYTES`). Note: the slice deliberately defers
  full auth + multi-tenancy + container sandboxing per spec
  Non-goals (lines 34-46); customers still reach the backend via the
  existing out-of-band whitelist.

### Migration notes

- Stop the old supervisor on the GPU host: `bash server/supervise.sh stop`
  (one last time before the script is gone).
- Install the systemd unit per `deploy/README.md`:
  ```bash
  sudo systemctl link "$(pwd)/deploy/gsfluent-backend.service"
  sudo systemctl enable --now gsfluent-backend.service
  ```
- Set the cap env-vars in the systemd `Environment=` section (or in
  the `EnvironmentFile=` the unit points at). The defaults are safe;
  override only if your workload needs different limits.
- Existing `.gsq` cache files are forward-compatible — the streaming
  cache will start serving ETags from them on the next read.

---

```

Note: this is a `[Unreleased]` entry — Phase 7's PR doesn't bump the version. Whoever cuts the next release tag should rename `[Unreleased]` to `[1.0.0]` (or whichever version) and add a release date.

- [ ] **Step 2: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add CHANGELOG.md
git commit -m "phase-7: CHANGELOG — Keep-a-Changelog format; capture bulletproofing slice's user-visible changes"
```

---

### Task 22: Extend CI to run lint + typecheck + the new test categories

**Files:**
- Modify: `.github/workflows/ci.yml`

The existing `ci.yml` has a `compose-config`, `api-tests`, and `frontend-build` job — none of those exercise the bulletproofing slice. Add a `gsfluent-backend` job (or several) that runs the new test categories + ruff + mypy.

- [ ] **Step 1: Read the current ci.yml in full**

Already audited at planning time — 103 lines, three jobs (`compose-config`, `api-tests`, `frontend-build`). The existing `api-tests` job runs against `apps/api/`, not `server/`. The bulletproofing-slice jobs are additions, not replacements.

- [ ] **Step 2: Append new jobs**

Use `Edit` to append four jobs after the `frontend-build` job. Open `ci.yml`, find the last line (`          retention-days: 7`), and append after it.

new_string (the full new job block to add at the end of `ci.yml`):

```yaml

  gsfluent-unit:
    name: gsfluent unit + protocol conformance
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install gsfluent dev deps
        working-directory: server
        run: |
          python -m pip install --upgrade pip
          pip install -e '.[dev]'

      - name: Run unit + conformance + e2e tests
        working-directory: server
        run: |
          PYTHONPATH=. pytest \
            tests/protocols/ \
            tests/observability/ \
            tests/core/ \
            tests/codecs/ \
            tests/sim_engines/ \
            tests/storage/ \
            tests/fusers/ \
            tests/runs/ \
            tests/api/ \
            tests/e2e/ \
            tests/test_config.py \
            tests/test_composition.py \
            -v --tb=short

      - name: Re-run baseline tests (no-regression gate)
        working-directory: server
        run: |
          PYTHONPATH=. pytest \
            tests/test_cells.py \
            tests/test_coord_convert.py \
            tests/test_frame_stream.py \
            tests/test_health.py \
            tests/test_library_smoke.py \
            tests/test_models.py \
            tests/test_recipes.py \
            tests/test_runner.py \
            tests/test_runs_api.py \
            tests/test_schemas.py \
            tests/test_sequences_import.py \
            tests/test_zup_invariant.py \
            -v --tb=short

  gsfluent-integration:
    name: gsfluent integration (mock_sim)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install gsfluent dev deps
        working-directory: server
        run: |
          python -m pip install --upgrade pip
          pip install -e '.[dev]'

      - name: Run integration tests (uses mock_sim.sh, no GPU required)
        working-directory: server
        run: |
          PYTHONPATH=. pytest tests/integration/ -v --tb=short

  gsfluent-lint:
    name: gsfluent ruff
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install gsfluent dev deps
        working-directory: server
        run: |
          python -m pip install --upgrade pip
          pip install -e '.[dev]'

      - name: ruff check
        working-directory: server
        run: ruff check gsfluent/ tests/

  gsfluent-typecheck:
    name: gsfluent mypy --strict
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install gsfluent dev deps
        working-directory: server
        run: |
          python -m pip install --upgrade pip
          pip install -e '.[dev]'

      - name: mypy --strict gsfluent/
        working-directory: server
        run: mypy gsfluent/
```

Note: per spec line 686-687, property tests are deferred to a nightly job and GPU tests stay manual. This CI block matches that — `gsfluent-property` is not added here. If the team later wants nightly property tests, a separate scheduled workflow file is the right move.

- [ ] **Step 3: Sanity-check the YAML**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML valid"
```

Expected: `YAML valid`. If YAML errors, fix indentation (most common: tab vs 2-space mix).

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add .github/workflows/ci.yml
git commit -m "phase-7: ci — add gsfluent unit / integration / lint / typecheck jobs"
```

---

### Task 23: Final full-suite re-run after all edits + push the branch

**Files:**
- Modify: `docs/superpowers/phase-7-verification-log.md` (append under "## Final test re-run")

After the README + ARCHITECTURE + ruff + mypy + CI edits, run the full test suite one more time to confirm nothing broke.

- [ ] **Step 1: Re-run the full suite**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
PYTHONPATH=. .venv/bin/python -m pytest tests/ -q --tb=short 2>&1 | tee /tmp/phase-7-final.txt | tail -15
```

Expected: same pass count as Task 8's aggregate. If the count differs from Task 8, find the diff (the only edits since Task 8 were docs/config/CI — none of those should affect pytest collection or pass count).

- [ ] **Step 2: Append the final result to the verification log**

Append under `## Final test re-run`:
- The exact pytest invocation.
- The final `===== N passed in Xs =====` line.
- A one-line confirmation: "Final pass count matches Task 8 aggregate: N tests, X seconds."

- [ ] **Step 3: Re-run ruff and mypy one last time**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/server
.venv/bin/ruff check gsfluent/ tests/
.venv/bin/mypy gsfluent/
```

Expected: `All checks passed!` from ruff and `Success: no issues found in N source files.` from mypy.

- [ ] **Step 4: Commit and push**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add docs/superpowers/phase-7-verification-log.md
git commit -m "phase-7: final test re-run — full suite green, ruff clean, mypy --strict clean"

git push -u origin phase-7-done-sweep
```

Expected: branch published on origin. Open the PR — see Task 24 for the title and body.

---

### Task 24: Open the Phase 7 PR

**Files:**
- No file edits. PR creation via `gh`.

- [ ] **Step 1: Open the PR**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
gh pr create --title "phase-7: definition-of-done sweep — bulletproofing slice ships" --body "$(cat <<'EOF'
## Summary

Phase 7 of 7 in the backend bulletproofing slice. Verification + docs +
ship signal. No production code changes (other than ruff/mypy auto-fixes
and — if needed — the `sim.unstable_recipe` classifier from spec Open
Question 1).

- Every test category re-run with counts captured in
  `docs/superpowers/phase-7-verification-log.md`.
- `ruff check` and `mypy --strict` both clean on `gsfluent/`.
- Five manual verifications completed: kill -9 mid-sim, happy-path
  journalctl event chain, fresh systemd install, cap-violating recipe
  rejected without subprocess, streaming cache hit on second load.
- README (zh + en) and `docs/ARCHITECTURE.md` updated for the
  six-Protocol layout, systemd, cap-config env vars.
- `CHANGELOG.md` created with the slice's user-visible changes.
- CI extended with `gsfluent-{unit,integration,lint,typecheck}` jobs.
- Open Question 1 (`sim.unstable_recipe` classifier) resolved per spec
  default (YAML-parametrized patterns); decision recorded in the
  verification log.

## Test plan

- [ ] CI passes all four new gsfluent jobs (`unit`, `integration`, `lint`, `typecheck`)
- [ ] Existing CI jobs (`compose-config`, `api-tests`, `frontend-build`) still pass
- [ ] Reviewer reads `docs/superpowers/phase-7-verification-log.md` and confirms the manual-verification evidence is convincing
- [ ] Reviewer skims the README and CHANGELOG updates and confirms they match the slice's actual behavior

## Definition of Done

See `docs/superpowers/plans/2026-05-22-phase-7-done-sweep.md` — every
spec Definition-of-Done bullet maps to one or more tasks in this PR.

Closes the backend bulletproofing slice.
EOF
)"
```

Expected: PR URL printed. Record it in the verification log.

- [ ] **Step 2: Mark the spec as fully implemented (optional)**

Edit `docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md` line 4 (Status):

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
# (use Edit tool to change the Status line to include "Phases 1-7 implemented; merged at <date>")
```

Commit and push:

```bash
git add docs/superpowers/specs/2026-05-22-backend-bulletproofing-vertical-slice-design.md
git commit -m "phase-7: mark spec as fully implemented (Phases 1-7 merged)"
git push
```

---

## Definition of Done — Phase 7

Phase 7 ships when ALL of the following are true. The first six bullets are quoted verbatim from the spec's "Definition of done" (Section "Testing strategy" > "Definition of done", lines 690-700):

- [ ] **All Protocol conformance tests pass for current concrete impls** (Task 3 — `tests/protocols/` green)
- [ ] **Every integration test passes locally + in CI** (Task 4 + Task 22's `gsfluent-integration` job green)
- [ ] **Existing `test_runner.py`, `test_runs_api.py`, `test_sequences_import.py`, `test_zup_invariant.py`, etc. still pass (no regressions across the refactor)** (Task 7 — 12 baseline files re-run green)
- [ ] **Manual: kill -9 the backend mid-sim, restart, run resumes as `interrupted`** (Task 13 — evidence in verification log)
- [ ] **Manual: `journalctl -u gsfluent-backend -o json | jq` shows structured events for a happy-path run** (Task 14 — event chain captured)
- [ ] **systemd unit deployed; `systemctl status gsfluent-backend` shows active and recent restart count = 0** (Task 15 — `NRestarts=0`, watchdog firing)
- [ ] **README / deploy docs updated for systemd install** (Task 19 — both `README.md` and `README.en.md`; Task 20 — `docs/ARCHITECTURE.md`)

Plus the prompt's additions (not in spec but in scope):

- [ ] **`ruff check gsfluent/ tests/` reports `All checks passed!`** (Task 11)
- [ ] **`mypy --strict gsfluent/` reports `Success: no issues found`** (Task 12)
- [ ] **Cap-violating recipe returns 422 with structured envelope; no subprocess; no `_state/runs/<id>.json` created** (Task 16)
- [ ] **Streaming cache hit on second load emits `cell.cache.hit` event (or no full GET follows the HEAD)** (Task 17)
- [ ] **`sim.unstable_recipe` classifier resolved per spec Open Question 1 — either confirmed-as-implemented or added as a 1-2 hour follow-up** (Task 18 — verification log records which branch was taken)
- [ ] **`CHANGELOG.md` lists every user-visible change of the bulletproofing slice** (Task 21)
- [ ] **CI runs gsfluent's unit + integration + ruff + mypy jobs** (Task 22)
- [ ] **Final full-suite re-run matches Task 8 aggregate; ruff + mypy still clean** (Task 23)
- [ ] **PR opened with the body from Task 24; verification log linked from the PR** (Task 24)

## Handoff after Phase 7

This is the last plan in the slice. After Phase 7 merges:

1. **Cut a release tag.** Whoever cuts the next release: rename `[Unreleased]` in `CHANGELOG.md` to `[1.0.0]` (or the chosen version) with the release date.
2. **Run the verification log on a clean dev box** before announcing the release publicly — Task 13/14/15/16/17 are reproducible by construction; redo them on a box that wasn't the development box to catch host-specific dependencies.
3. **Schedule the nightly property-test job.** The spec defers property tests to nightly (line 686); Phase 7 added them as a manual `pytest tests/property/` run. A scheduled GitHub Actions workflow that runs `pytest tests/property/ --hypothesis-show-statistics` weekly is a small follow-up.
4. **Tackle the next sprint from spec's "Out-of-spec follow-ups" list** (lines 785-794), starting with the data integrity sprint (`.gsq` checksums, intermediate `.ply` retention controls, K-NN map robustness) — the highest-leverage post-slice work.

---

**End of Phase 7 plan. End of backend bulletproofing slice.**
