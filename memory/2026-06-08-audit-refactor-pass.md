# Audit Refactor Pass

Date: 2026-06-08

Scope:
- Refactored the configured work-root boundary.
- Fixed frontend unit-test discovery.
- Synced the critical backend path refactor to the remote server and restarted it.

Findings:
- `AppConfig` and health used `GSFLUENT_WORK_DIR`, but library/cache/user-recipe
  code still derived paths from `PKG_ROOT/work`.
- `MPMSimulationEngine.run()` accepted `output_dir` but ignored it, rebuilding
  `PKG_ROOT/work/library/sequences/<run>` internally.
- `npm test -- --run` included Playwright `e2e/*.spec.ts`, causing Vitest to fail.

Fixes:
- `_paths.py` now derives `WORK` from `GSFLUENT_WORK_DIR` when present.
- `config.py`, `library.py`, `recipes.py`, `api/sequences.py`, and MPM output
  routing now use the centralized path constants or explicit `output_dir`.
- `api/runs.py` injects `_output_dir = lib.SEQUENCES_DIR / run_name` before
  submitting a run.
- `frontend/vite.config.ts` excludes `e2e/**` from Vitest.

Verification:
- `python -m compileall -q gsfluent` passed.
- Targeted backend regressions passed: `9 passed`.
- `npm test -- --run` passed: 36 frontend unit tests.
- `npm run build` passed.
- Remote backend restarted; `/api/health` is `ok`; `/api/compose/library` returns
  HTTP 200.

Remaining audit backlog:
- Backend local env lacks `ruff` and `mypy`, so lint/type gates could not be run.
- Several modules still document `work/library/...`; those are conceptual layout
  references, not hardcoded path bugs, but docs should eventually say
  "under GSFLUENT_WORK_DIR".
- Playwright e2e still needs a real browser run separately from Vitest.
