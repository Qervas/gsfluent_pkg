# Scenario Visual Editor — Design

**Status:** draft for review
**Date:** 2026-05-18
**Author:** workbench team
**Replaces:** none (new feature)
**Touches:** frontend only

---

## Goal

Replace hand-editing JSON for `boundary_conditions` with a visual,
parametric scenario editor. The user picks a scenario type (Earthquake,
Impact, Uplift, Collapse, or Custom), tunes a handful of physically
meaningful params, and watches gizmos in the viewport reflect what the
sim will see. Clicking Run submits the same flat BC list the backend
already consumes — no backend changes.

## Why now

Today's `earthquake.json` (and the other scenarios) is a frozen, opaque
BC list. Tuning means hand-editing JSON; the only way to ask "what
would happen if the shake was 2× stronger?" is to rewrite four cuboid
entries by hand. Three of the four scenarios feel stub-y because the
recipe doesn't expose the physical parameters that actually matter
(PGA, frequency, impactor velocity, …).

## Non-goals

- No new backend endpoints or sim features. Backend integrates BCs.
- Not redesigning the recipe format beyond an optional metadata block.
- Not changing materials, gravity, n_grid, or other non-scenario params.
- No multi-user / collaborative editing.
- v1 does not animate the gizmos through time — they show start-state.
  Timeline scrubbing is a future iteration.

---

## Architecture

```
                     ┌─ Scenario params (PGA, freq, axis, dur, …)
                     │
EARTHQUAKE preset ───┼─→ TS generator fn ─→ BC[]
IMPACT     preset ───┤
UPLIFT     preset ───┤
COLLAPSE   preset ───┤
                     │
CUSTOM mode ─────────┴─ User-edited BC[] (gizmo drags)
                                       │
                            recipe.boundary_conditions
                            recipe._scenario {kind, params}  ← optional
                                       │
                                       ▼
                            POST /api/runs/start (unchanged)
```

The backend sees `boundary_conditions: [...]` exactly as it does now.
The `_scenario` block is a hint for the frontend on reload; the backend
ignores any unknown top-level keys (already true today).

### Layers

| Layer | Responsibility |
|---|---|
| Scenario generators | Pure TS functions: `(params, simArea) → BoundaryCondition[]` |
| Editor store slice | Holds `{kind, params, customBCs, simArea}` for the active draft |
| Viewport overlay | Transparent three.js Canvas atop the viser iframe; renders sim-domain wireframe + gizmos |
| Scenario sheet | Right-side panel with scenario type dropdown + param form |
| Timeline strip | 1D strip below the viewport; one bar per BC; drag endpoints to retime |
| Recipe round-trip | `_scenario` getter/setter on recipe load/save |

---

## Scenario types

### Earthquake

**Params:**
| Field | Range | Default | Meaning |
|---|---|---|---|
| `pga` | 0.05–1.0 g | 0.3 g | Peak ground acceleration |
| `freq` | 0.5–10 Hz | 2.0 Hz | Dominant frequency |
| `duration` | 0.5–6 s | 3.0 s | Total shake duration |
| `axis` | "x" \| "y" \| "xy" | "x" | Horizontal motion axis |
| `decay` | "none" \| "linear" \| "exp" | "exp" | Amplitude envelope |

**Generator behavior:**
Half-period of motion = `0.5 / freq`. The floor's horizontal velocity
is a sinusoid sampled at half-period steps. Each step becomes one
`cuboid` BC with constant velocity over `[t, t+0.5/freq]`. Amplitude
decays per `decay`. Cuboid size = floor footprint (derived from sim_area).

For `axis="xy"`, alternating half-periods drive X then Y.

**Generated BC count:** roughly `2 * freq * duration` cuboids
(e.g., 2 Hz × 3 s = 12 cuboids).

### Impact

**Params:**
| Field | Range | Default | Meaning |
|---|---|---|---|
| `position` | 3 floats | sim_area top center | Impactor start position |
| `size` | 3 floats | [0.2, 0.2, 0.2] | Impactor box dimensions |
| `velocity` | 3 floats | [0, 0, -10] | Impact velocity vector |
| `start_time` | 0–5 s | 0.1 s | When the impactor activates |
| `duration` | 0.01–2 s | 0.3 s | How long the impactor is present |

**Generator:** one `cuboid` BC with the given fields and
`end_time = start_time + duration`.

### Uplift

**Params:** same shape as Impact, defaults differ:
- `position` = sim_area bottom center
- `velocity` = [0, 0, +0.5]
- `duration` = 1.0 s

Same generator. Distinction from Impact is UX (default placement +
gizmo affordance: vertical arrow only).

### Collapse

**Params:**
| Field | Range | Default | Meaning |
|---|---|---|---|
| `release_axis` | "+z" \| "-z" | "-z" | Which direction the release sweeps |
| `start_time` | 0–5 s | 0 s | Begin releasing particles |
| `duration` | 0.5–10 s | 3.0 s | Total release sweep duration |
| `height_range` | [zmin, zmax] | sim_area Z bounds | Z range over which particles release |

**Generator:** one `release_particles_sequentially` BC with these
fields. (Backend already knows this BC type.)

### Custom

No generator. User adds individual BC entries by hand — current options
are `cuboid` and `release_particles_sequentially`. Each is a draggable
gizmo. Switching to Custom from a preset copies the preset's generated
BC list as the starting point, then drops `_scenario.kind` to `"custom"`.

---

## Recipe format change

Add an optional `_scenario` block:

```json
{
  "material": "watermelon",
  "boundary_conditions": [...],
  "_scenario": {
    "kind": "earthquake",
    "params": {
      "pga": 0.3,
      "freq": 2.0,
      "duration": 3.0,
      "axis": "x",
      "decay": "exp"
    }
  },
  ...
}
```

**Load behavior:**
1. If `_scenario.kind` is recognized, regenerate BCs from params (ignore
   the on-disk BC list — single source of truth is params + generator).
2. If `_scenario` is absent, attempt heuristic detection (see below).
3. If detection fails or yields ambiguous match, fall back to Custom
   with the on-disk BCs verbatim.

**Save behavior:**
- Preset mode → write `_scenario` block + regenerated BCs.
- Custom mode → omit `_scenario` block; write BCs as edited.

**Backwards compatibility:** the existing 9 builtin recipes don't have
`_scenario` blocks. Heuristic detection handles them on load. We won't
rewrite them — they continue to work as Custom recipes with detected
scenario type displayed as a hint.

---

## Heuristic detection (fallback for recipes without `_scenario`)

When loading a recipe with no `_scenario` block, classify the BC list:

| Pattern | Verdict |
|---|---|
| Contains `release_particles_sequentially` | Collapse (best-effort params) |
| ≥2 `cuboid` BCs alternating velocity sign on one axis | Earthquake (params inferred from sign-flip count + duration) |
| 1 `cuboid` with `velocity.z > 0` and start near floor | Uplift |
| 1 `cuboid` with high `|velocity|` and short window | Impact |
| Anything else | Custom |

Detection is best-effort. The UI shows a "Detected as Earthquake — open
form?" hint with an explicit accept button; we don't auto-coerce.

---

## UI surface

### Entry point

A new "Edit scenario" button in the Sim card, next to the Recipe
dropdown. Clicking opens the Scenario sheet.

### Sheet layout

```
+----------------------------------------------------------+
| Scenario: [ Earthquake ▾ ]                    [ Done ]   |
+----------------------------------------------------------+
|                                                          |
|  ┌──────────────────────────────────────┐  ┌──────────┐  |
|  │                                      │  │  PARAMS  │  |
|  │      [viser splat + sim-domain       │  │          │  |
|  │       wireframe + cuboid gizmos]     │  │  PGA     │  |
|  │                                      │  │  [0.30]g │  |
|  │      ↑ velocity arrows               │  │          │  |
|  │                                      │  │  Freq    │  |
|  │                                      │  │  [2.0]Hz │  |
|  │                                      │  │          │  |
|  │                                      │  │  Duration│  |
|  │                                      │  │  [3.0]s  │  |
|  │                                      │  │          │  |
|  │                                      │  │  Axis    │  |
|  │                                      │  │  (●)X    │  |
|  │                                      │  │  ( )Y    │  |
|  │                                      │  │  ( )XY   │  |
|  │                                      │  │          │  |
|  │                                      │  │  Decay   │  |
|  │                                      │  │  [exp ▾] │  |
|  └──────────────────────────────────────┘  └──────────┘  |
|                                                          |
|  ┌────── Timeline ──────────────────────────────────┐    |
|  │ cuboid #1  ████                                  │    |
|  │ cuboid #2     ████                               │    |
|  │ cuboid #3        ████                            │    |
|  │ cuboid #4           ████                         │    |
|  │ ...                                              │    |
|  │                                                  │    |
|  │ 0s        1s        2s        3s        4s       │    |
|  └──────────────────────────────────────────────────┘    |
+----------------------------------------------------------+
```

The Sim card and Source card are hidden while the sheet is open. The
viewport stays viser-driven (the splat is still visible underneath).
"Done" closes the sheet and commits the BCs to the active recipe in
memory; the user then clicks Run as usual.

### Viewport overlay

A transparent `<canvas>` is layered above the viser iframe with the
same dimensions. The overlay's three.js camera mirrors viser's camera
via 30 Hz polling of `GET /camera` (already exposed). Rendered objects:

1. **Sim-domain wireframe** — accent-colored edges, faintly tinted
   faces. Pulled from recipe's `sim_area` field. Hidden when sheet is
   closed.
2. **Cuboid gizmos** — one box per `cuboid` BC, TransformControls
   attached. Position + size editable via drag. Color matches the
   timeline bar for that BC.
3. **Velocity arrows** — one per cuboid, length proportional to
   velocity magnitude, drag the head to set vector.
4. **Release planes** — one rectangle per `release_particles_sequentially`
   BC, showing the height range and sweep direction.

Pointer events only register on the overlay when the cursor is over a
gizmo handle (raycaster check); otherwise events pass through to the
viser iframe so the user can still orbit the camera.

### Timeline

Below the viewport, 80px tall. Each BC gets one row. Bars span
`[start_time, end_time]`. Drag endpoints to retime; drag middle to
shift. Click a bar to highlight its gizmo (pulsing outline).

---

## Frontend state

A new Zustand slice:

```ts
type ScenarioKind = "earthquake" | "impact" | "uplift" | "collapse" | "custom";

type ScenarioDraft = {
  kind: ScenarioKind;
  // One of these is meaningful at a time, selected by `kind`:
  earthquakeParams?: EarthquakeParams;
  impactParams?:     ImpactParams;
  upliftParams?:     UpliftParams;
  collapseParams?:   CollapseParams;
  // For Custom mode, the raw BC list:
  customBCs?: BoundaryCondition[];
};

interface Store {
  scenarioDraft: ScenarioDraft | null;
  scenarioSheetOpen: boolean;
  setScenarioDraft: (d: ScenarioDraft) => void;
  setScenarioSheetOpen: (open: boolean) => void;
}
```

`scenarioDraft` mirrors what the editor is showing. The active recipe's
BC list is regenerated from the draft on every change (debounced 50ms)
and pushed back into `activeRecipeData.boundary_conditions`. When the
user clicks Run, the existing run-submission flow picks up the updated
recipe and submits.

---

## File layout

```
frontend/src/components/scenario/
  ScenarioSheet.tsx          — top-level sheet, dropdown + Done button
  ScenarioParamsPanel.tsx    — right panel (param form, varies by kind)
  ScenarioOverlay.tsx        — transparent three.js Canvas
  ScenarioTimeline.tsx       — bottom timeline strip
  gizmos/
    CuboidGizmo.tsx          — TransformControls box + velocity arrow
    ReleasePlane.tsx         — release_particles_sequentially gizmo
    SimDomainWireframe.tsx   — sim_area cube
  forms/
    EarthquakeForm.tsx
    ImpactForm.tsx
    UpliftForm.tsx
    CollapseForm.tsx

frontend/src/lib/scenarios/
  types.ts                   — ScenarioKind, *Params, BoundaryCondition
  earthquake.ts              — earthquakeToBCs(params, simArea)
  impact.ts                  — impactToBCs(params, simArea)
  uplift.ts                  — upliftToBCs(params, simArea)
  collapse.ts                — collapseToBCs(params, simArea)
  detect.ts                  — heuristic BC[] → ScenarioKind classifier
  index.ts                   — barrel + dispatcher

frontend/src/lib/store.ts    — add ScenarioDraft slice
```

---

## Tech choices

- **Three.js + @react-three/fiber** for the overlay. R3F is already in
  the bundle (was used pre-Phase-4); reintroducing it adds ~30 KB
  gzipped to the current 134 KB bundle.
- **TransformControls** from `three/examples/jsm/controls/TransformControls`.
  Drei wraps it but plain three works fine.
- **No new dependencies** for math — we already have numpy-equivalent
  needs covered by plain TS arithmetic.

---

## Risks and open questions

### Camera mirroring lag

Polling /camera at 30 Hz means gizmos lag the splat during fast orbit.
For v1, accept the lag. If it feels bad, the fix is to add a WS or SSE
push from viser_headless to the overlay; not in scope for v1.

### Pointer event passthrough

Overlaying a canvas while still letting users orbit the splat through
it requires careful event handling. Standard approach:
`pointer-events: none` on the canvas by default; raycaster check on
`onPointerMove` upgrades to `pointer-events: auto` when over a gizmo
handle. Documented pattern, low risk.

### Generator round-trip vs server tolerance

The `_scenario` field is unknown to the backend's recipe validator. The
validator must tolerate unknown fields (passing them through silently).
If today's validator rejects unknown keys, this is a 1-line server
change — needs verification before plan execution.

### Custom mode editing existing recipes

When a user opens a recipe with a detected scenario and starts dragging
gizmos in a way that breaks the pattern (e.g., changes one of the 12
earthquake cuboids out of phase), we drop them into Custom mode and
clear `_scenario`. This must be telegraphed clearly — a confirmation
dialog or banner.

---

## Out of scope for v1

- Timeline scrubbing (animating the sim through time in the preview)
- Saving scenario presets to the user recipe library
- Multi-scenario composition (e.g., earthquake AND meteor)
- Mobile / small-screen layouts
- Undo/redo within the sheet (planned for v2)
