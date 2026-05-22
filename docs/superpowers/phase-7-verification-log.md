# Phase 7 verification log

Scratchpad for raw outputs captured during the done-sweep. Each task in
`docs/superpowers/plans/2026-05-22-phase-7-done-sweep.md` that produces
evidence (test counts, journalctl excerpts, systemctl status, ps output)
appends here. The PR description summarizes; this file keeps the raw
proof for the next time a similar verification is needed.

---

## Baseline test count

Task 0 Step 5:

```
$ cd server && PYTHONPATH=. .venv/bin/python -m pytest tests/ --collect-only -q | tail -5
...
tests/test_zup_invariant.py::test_convert_full_3dgs_ply_makes_z_the_tall_axis
tests/test_zup_invariant.py::test_parse_frame_xyz_no_extra_rotation_on_zup_file
tests/test_zup_invariant.py::test_parse_static_attrs_no_basis_composition_on_zup_file

368 tests collected in 0.25s
```

Floor for Phase 7: 368 collected. Phase 6 baseline: 364 pass / 3 skipped /
1 pre-existing fail (`tests/test_library_smoke.py::test_sequence_load_returns_meta_and_frames`).
Phase 7 must not reduce the pass count or skip a previously-passing test
without an explicit rationale.

---

## Test category results

### Pre-existing baseline (no-regression gate)

(Task 7 output.)

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

## Full suite — aggregate

(Task 8 output.)

---

## Final test re-run

(Task 23 output.)
