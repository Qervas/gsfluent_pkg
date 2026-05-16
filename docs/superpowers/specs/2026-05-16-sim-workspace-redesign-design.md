# Sim workspace redesign — design

**Date:** 2026-05-16
**Status:** approved (verbal)
**Scope:** Sim workspace IA + Properties panel rebuild. Recipes workspace becomes a modal overlay. No backend changes.

## Goal

Fix three concrete user complaints:

1. The current Sim workspace conflates **reading** (load a model / view a sequence) with **simulating** (pick a recipe + run). They're cognitively different tasks but share one tabbed Outliner.
2. The Properties panel is "not scientific, not intuitive" — flat folders, linear sliders for log quantities, no unit hints, no diff between recipe baseline and user edits, no visible feedback on what changed.
3. Switching to the Recipes workspace remounts the entire viewport (model upload, scene rebuild, camera fit) — destructive churn for a workflow that should be a side trip.

## Resource model (unchanged; surfaced honestly for the first time)

```
Model        ← static .ply + cameras.json (uploaded once)
  ├── run    ← (Model + Recipe + overrides) → sim execution
  │     └── Sequence   ← .npz of per-frame splats; carries `model_ref` + `recipe_source`
  │
Recipe       ← sim parameters; named, library-resident, owned by user or builtin
Orphan       ← Sequence with `model_ref = null` (imported .npz)
```

One Model can produce N Sequences via N runs against M Recipes. The current Outliner shows Models / Sequences / Recipes / Runs as four parallel flat lists; the redesign collapses Models + Sequences into a model-rooted tree and leaves Recipes as a separate library.

## Workspace layout

```
┌── TopBar ──────────────────────────────────────────────────┐
│  gsfluent · breadcrumb · 📚 Recipes (toggle) ·  ▶ Run       │
├──────────────────────────┬──────────────────────────────────┤
│ ① Source card            │                                  │
│   tree: models →         │       VIEWPORT                   │
│         sequences        │       (full right side)          │
│   + new sim per model    │                                  │
├──────────────────────────┤                                  │
│ ② Simulation card        │                                  │
│   recipe picker          │                                  │
│   Form ↔ JSON toggle     │                                  │
│   ScientificInput params │                                  │
│   override accents + ⤺   │                                  │
│   Run · Save as · Reset  │                                  │
└──────────────────────────┴──────────────────────────────────┘
                                                  StatusPanel ▼
```

- Both cards live on the **left** as a single column stack (`Source` on top, `Simulation` below).
- The right side has **no glass card** — viewport gets the full width.
- StatusPanel (existing floating pill) stays bottom-right.
- The current right-side `Properties` glass card is **removed** from the Sim workspace; its content moves into the Simulation card.

## ① Source card

Replaces the current Outliner tabs (Models / Sequences / Runs / Recipes are no longer parallel).

**Structure:**

```
Models
  ▾ tower_01                                3 runs
        tower_01_jelly_2026-05-15    [jelly]   5d   ▶
        tower_01_sand_2026-05-15     [sand]    5d   ▶
        tower_01_jelly_v2_2026-05-16 [jelly_v2] 2m   ▶
        + new simulation from tower_01
  ▸ building_b                              1 run

Orphan sequences  (no parent model)
        imported_jelly_smash         [imported]    ▶
```

**Interactions:**

| Click target | Effect |
|---|---|
| Model row | Selects model. Simulation card opens with recipe picker. Viewport shows static splat preview. |
| Sequence row (under a model) | Loads sequence for playback. Simulation card collapses to read-only summary citing the recipe + any baked-in overrides that produced it. |
| `+ new simulation from <model>` | Ensures model is selected, focuses the Simulation card's recipe picker. |
| Orphan sequence row | Loads for playback. Simulation card is hidden entirely (no parent model to re-run from). |
| Recipes (in TopBar) | Opens the Recipes modal (see below). |

**Data source:**
- Models come from `GET /api/models`.
- Sequences from `GET /api/sequences`; group by `model_ref`. Sequences with `model_ref === null` go in the Orphan group.
- The tree's open/closed state per model persists to `localStorage.gsfluent.outliner_tree`.

## ② Simulation card

**Disabled** when Source is a Sequence or nothing. **Read-only summary** when Source is a sequence (shows `Recipe: jelly`, `Overrides: 3 (E, ν, gravity_z)` with a tooltip listing them).

**Header:**

```
② Simulation                                          [2 overrides]
Recipe: [ jelly ▾ ]                          Form ◉ ──── JSON ○
```

- Recipe dropdown lists builtins first, then user recipes (★). Changing it shows a confirm dialog if `overrides` is non-empty: *"Discard 3 overrides?"* Discard / Cancel.
- The Form/JSON toggle switches the body view. State persists per session in `localStorage.gsfluent.sim_view_mode`.
- The `[N overrides]` badge in the header is accent-colored and clickable — click to expand a list of which params are overridden.

**Form body:**

- Uses the existing `ScientificInput` widget (log-scale sliders with reference markers, unit chips, hint tooltips).
- Material-gated visibility from the existing `MATERIAL_FIELDS` map (sand hides yield_stress; jelly hides friction; etc.).
- Each param row format:

  ```
  Young's E                       8,200 sim  ⤺
  ●━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━━●  (slider)
  ↑ jelly  ↑ firm           ↑ metal           (markers)
  ```

  The value `8,200` is **accent-colored** because it differs from the recipe baseline of `5,000`. The `⤺` next to it reverts that single field. Non-override values render in default text color with no `⤺`.

- Folders (`▾ Material`, `▾ Forces`, `▸ Solver`, etc.) remain, but their headers carry an override-count badge (`▾ Material [1]`) so a collapsed folder still surfaces unsaved deviations.

**JSON body:**

- Shows the **merged effective** config (recipe baseline + overrides applied).
- Override lines get an accent left border + a trailing comment: `// override (recipe: 5000)`.
- The editor is read-write. Saving the JSON re-computes overrides:
  - For each top-level key, if the value differs from the recipe baseline, that key is added to `overrides`; otherwise it's removed.
  - Syntax error → Form is locked behind a banner *"JSON has errors; Form shows last parse"* and Run is disabled.
  - Keys that exist in JSON but have no matching Form widget are preserved as opaque overrides (some recipes carry params we haven't built widgets for); shown in JSON only.

**Footer:**

```
[ ▶ Run ]   [ Save as new recipe… ]   [ Reset all overrides ]
```

- `Run` is the primary action. Disabled if no model selected. Sends `POST /api/runs` with `recipe_data = effective`, `recipe_source = <baseline recipe name>`. Run state surfaces in the existing RunButton 5-state machine.
- `Save as new recipe…` opens a small inline prompt for a name. Server gets `PUT /api/recipes/<name>` with `effective`. New recipe appears in the library and becomes the new baseline; `overrides` clears.
- `Reset all overrides` clears `overrides`. Confirmation only if there are 3+ overrides (to prevent slips).

## Override semantics

Three concepts in store, per active recipe:

| Slot | Owner | Lifetime |
|---|---|---|
| `recipe_baseline` | The selected recipe; immutable | Until recipe changes |
| `overrides` | Sparse `{ key: value }` map of user edits | Cleared on recipe switch, Reset, Save as new, page reload |
| `effective` | Computed `{...baseline, ...overrides}` | Recomputed on every change |

**Persistence:** `overrides` lives only in the Zustand store — not on disk, not on the server. Page reload wipes them. This is intentional: it forces the user to either `Save as new recipe…` or accept that their tweak is single-use. Keeps "what's loaded" honest, avoids stale per-session state confusing future runs.

**Recipe deletion (edge case):** if the active recipe is deleted (via the Recipes modal) while there are overrides, the Simulation card shows a yellow banner: *"Baseline 'jelly' was deleted. Your 3 overrides are now standalone."* The overrides become `effective` directly; Run still works; `Save as new recipe…` becomes the obvious next action.

## Recipes modal

Triggered by:
- Click the `Recipes` pill in the TopBar (was a tab; becomes a toggle button).
- Cmd/Ctrl-R global shortcut.

**Form:**
- Center-screen overlay. ~720×520 px on desktop. Backdrop blur over the viewport (no remount).
- ESC or click outside dismisses (with override-discard confirm if there are unsaved edits to the active recipe in the modal).

**Layout:**
- Left column (~200 px): library tree — `Built-in`, then `User saved (★)`, then `Import…` / `+ New` actions.
- Right column: detail editor for the selected recipe.

**Detail editor:**
- Title row: name + source tag (`read-only` for builtins, editable inline for user) + action cluster: `Duplicate`, `Rename` (user only), `Delete` (user only), `Use in Sim`, `✕`.
- Form/JSON toggle (same widget as the Simulation card).
- Body: ScientificInput grid in Form mode, syntax-highlighted JSON in JSON mode.
- Built-in recipes show a *"Duplicate to edit"* call-to-action above the body; editing is disabled.

**Use in Sim:** closes the modal, loads the selected recipe into the Simulation card. If the Simulation card had overrides, asks to discard first.

**Import / Export:**
- `Import…` opens a file picker; reads JSON, uploads via existing `PUT /api/recipes/<name>` (name prompted).
- `Export` button per user recipe: downloads `<name>.json` directly.

## State machine

| Source state | Sim card | Run button | Viewport |
|---|---|---|---|
| **Nothing loaded** | Empty state: *"Pick a model or sequence in the Source card."* | Disabled | Empty (placeholder grid) |
| **Model loaded, no recipe** | Recipe picker visible, params hidden, body says *"Pick a recipe to configure simulation."* | Disabled | Static splat preview |
| **Model + Recipe, idle** | Full param editor, override count badge | Enabled, accent gradient | Static splat preview |
| **Running** | All inputs grey, override count frozen | Orange · Loader · ETA · Cancel | Static splat preview (sequence appears under model in tree as frames arrive) |
| **Just finished** | Inputs re-enabled. Inline toast: *"Run finished — view sequence?"* button switches Source to the new sequence | Green flash 2s then Run-again | Static splat preview |
| **Sequence loaded** (under model) | Collapsed to read-only summary: *"Based on recipe `jelly` + 2 baked-in overrides (E=8200, gravity_z=-20)"*. A `New run from this recipe…` action button lives **inside the summary** (the card's normal Run-button footer is gone in this state). Clicking it switches Source to the parent model and pre-fills the Simulation card with the recipe + those overrides. | (action sits in the summary, see ←) | Sequence playback |
| **Orphan sequence loaded** | Hidden | Hidden | Sequence playback only |
| **Error** | Inputs re-enabled, baseline preserved, last log line surfaced in card footer | Red · *Retry* | Static splat preview |

## What this replaces / deprecates

- Existing `Outliner` (Models / Sequences / Runs / Recipes tabs): replaced by Source card (tree) + Recipes modal. The `Runs` tab content (run history) moves into the StatusPanel's console drawer; sequences in the library *are* the persistent record of runs.
- Existing right-side `Properties` glass card in Sim workspace: removed. Its panels move into the Simulation card.
- Existing `Recipes` workspace tab in `App.tsx`: the workspace switching mechanism is dropped from this surface; the `Recipes` chip in TopBar now toggles the modal. (Internal `activeWorkspace` state can stay, or be dropped — see "Out of scope.")
- The `FullWorkspaceShell` component: no longer needed if `activeWorkspace` is dropped. Otherwise leave it; it's used by other potential workspaces.

## What stays unchanged

- Backend API: no changes. `POST /api/runs`, `GET /api/models|sequences|recipes`, `PUT /api/recipes/<name>` all continue to do exactly what they do.
- `ScientificInput` widget and material gating: keep using as-is.
- `RunButton` 5-state machine: reused inside the Simulation card footer.
- `StatusPanel` pill + console drawer: positions adjust if Properties is gone, but logic unchanged.
- Drag-drop `.ply` / `.npz` upload via `DropZone`: still works window-wide.
- Viewport's `Viewport.tsx`, `SplatScene`, `ViserSplatScene`, playback bar: all unchanged.

## Files touched

**New:**
- `frontend/src/components/sim/SourceCard.tsx` — the model-rooted tree
- `frontend/src/components/sim/SimulationCard.tsx` — recipe picker + Form/JSON + params + actions
- `frontend/src/components/recipes/RecipesModal.tsx` — the full library manager modal
- `frontend/src/components/properties/widgets/JsonEditor.tsx` — syntax-highlighted JSON view shared by both surfaces
- `frontend/src/lib/use-overrides.ts` — hook computing `effective = {...baseline, ...overrides}` and providing setters
- `frontend/src/lib/store.ts` — add `simOverrides`, `simRecipeBaseline`, `recipesModalOpen` slices

**Replaced:**
- `frontend/src/components/layout/AppShell.tsx` — drop right-side Properties card, switch Outliner slot to Source card, add Recipes modal
- `frontend/src/components/properties/Properties.tsx` — repurposed as the body of SimulationCard (Form mode), thinner wrapper
- `frontend/src/components/outliner/Outliner.tsx` — most logic moves into SourceCard.tsx; this file probably deletable
- `frontend/src/workspaces/RecipesWorkspace.tsx` — most logic moves into RecipesModal.tsx; this file deletable
- `frontend/src/App.tsx` — drop the workspace-switching `<FullWorkspaceShell>` branch and just render `<AppShell>`. The `activeWorkspace` store field becomes vestigial; leave it (out of scope to prune — see below).

**Adjusted:**
- `frontend/src/components/viewport/RenderModeToggle.tsx` — track Outliner (now removed); slide logic simplifies
- `frontend/src/components/viewport/FpsIndicator.tsx` — same
- `frontend/src/components/viewport/DropZone.tsx` — same
- `frontend/src/components/layout/StatusPanel.tsx` — console drawer positioning, no longer needs to clear Properties

## Out of scope

- **Comparison view** between two sequences side-by-side. Mentioned as a "would be nice" earlier; not part of this redesign. Add later if the override workflow doesn't already cover it.
- **Multi-select** for runs (e.g., select 3 sequences from one model, play them in parallel). Defer.
- **Recipe versioning** (git-style history of a recipe). Defer.
- **Live re-run** while editing (auto-Run on parameter change). Defer; explicit Run button is fine for now.
- **Other workspaces** beyond Sim. The `activeWorkspace` enum can stay in the store as a one-value enum (`"sim"`) until/unless a future workspace lands. Pruning that machinery is out of scope.

## Implementation phases

1. **Phase 1 — Override engine.** Add `simOverrides` + `simRecipeBaseline` to the store. Refactor the existing Properties param widgets to read `effective` and write to `overrides`. Render override accents + `⤺`. Add `Save as new` and `Reset all`. Test in the current right-side Properties before any layout move. [≈1 day]

2. **Phase 2 — SourceCard.** Build the model-rooted tree. Replace the Outliner's content with it. Persist tree open/close state. Hook click handlers per the state-machine table. [≈1 day]

3. **Phase 3 — SimulationCard.** Combine the override-aware Properties body with the recipe picker, Form/JSON toggle, footer actions. Place under SourceCard in the left rail. Remove the right-side Properties glass card. [≈1 day]

4. **Phase 4 — RecipesModal.** Drop the workspace tab UI, replace with TopBar toggle + Cmd-R. Modal renders over the viewport with backdrop. Library list + detail editor share the Form/JSON widget from Phase 3. Import/Export wiring uses existing endpoints. [≈1 day]

5. **Phase 5 — JsonEditor + Form↔JSON parity.** Build the JSON editor (CodeMirror or a simple textarea + Prism-style highlighting) with the override-diff display. Wire bidirectional sync with the override engine. Handle syntax errors gracefully. [≈1 day]

6. **Phase 6 — Polish + edge cases.** Recipe-deleted banner, override-discard confirm, run-finished toast, modal Esc-with-unsaved confirm, keyboard nav for the tree. [≈0.5 day]

Total: ~5–6 working days end-to-end.

## Risks

- **CodeMirror or a custom JSON editor?** CodeMirror is ~150 KB; a custom textarea + Prism is ~30 KB but loses bracket matching, multi-line undo, etc. Recommendation: pick custom for now (matches "scientific tone" without fighting a heavy editor framework), upgrade to CodeMirror if the user later asks for IDE feel. Documented as a deferred decision rather than a blocker.

- **Tree state explosion** for users with 50+ models. The flat list under each model could become unwieldy; mitigate by lazy-rendering the sequence children only when the model is expanded, and limiting the per-model children to the most recent 20 with a "Show all" link. Defer the optimization until we hit it.

- **Loss of the Recipes workspace tab** removes a discoverable surface for new users. Mitigate by showing a one-time tooltip on the new TopBar Recipes pill: *"📚 Recipe library — Cmd-R"*.

- **Override-on-recipe-delete** ambiguity: we let the overrides become standalone, but a user who hits this case may not understand. The yellow banner copy must be exact and the Save-as-new action must be one click. Spec the copy in Phase 6.
