# gsfluent E2E tests

## Run

```bash
cd frontend
npm run e2e            # starts both servers + runs all specs
npm run e2e -- --ui    # debugger UI
```

## What's covered (smoke)

- `smoke.spec.ts::app boots and shell renders` — App shell, workspace tabs, Outliner sections, ⌘K hint.
- `smoke.spec.ts::command palette opens via ⌘K` — keyboard shortcut + cmdk palette mount.
- `smoke.spec.ts::Recipes section lists at least one built-in recipe` — Outliner ↔ backend integration. Skipped if backend offline.

## What's NOT covered (full MVP acceptance criteria — deferred)

The two acceptance criteria from the plan need real fixtures + a running
sim, which add significant test infrastructure:

1. **Casual flow:** drag a `.ply` → pick `jelly` → click Run → first frame
   visible within 90s. Requires:
   - A small reference `.ply` fixture in `e2e/fixtures/` (ideally <50k splats
     so the sim takes <30s on a CI runner).
   - Backend with the Taichi/Warp stack actually able to run on the host.
   - A way to assert "frame visible" — likely via `expect(canvas).toBeVisible()`
     plus `await page.waitForFunction(() => store.simNFrames > 0)` (need to
     expose store on `window` for tests).

2. **Power flow:** load a past run from History → frames + recipe restore →
   tweak `E` slider → Save preset → ★ entry appears in History within 10s.
   Requires:
   - Pre-baked fused frames in `work/fused/<run_name>/` (the project already
     has `pkg_smoke_test` and `R7.M_*_cluster` runs that could serve as
     fixtures).
   - WebSocket subscription to actually deliver frames in the test environment.

These belong in a `acceptance.spec.ts` file once the fixtures are stood up.
Track as a follow-up.
