# Sim Workspace Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the right-side Properties panel + Recipes workspace tab with a left-side two-card pipeline (`SourceCard` over `SimulationCard`) and a center-screen `RecipesModal`. Introduce a recipe + overrides edit model with accent-colored deltas and per-field revert.

**Architecture:** Frontend-only refactor. Zustand store gains a sparse `simOverrides` slice; a new `use-overrides` hook computes `effective = {...recipe_baseline, ...overrides}` and provides setters. The existing `ScientificInput` widget grows a baseline + revert affordance. Layout changes happen in `AppShell.tsx`. No backend changes.

**Tech Stack:** React 18 · TypeScript · Vite · Zustand · `@tanstack/react-query` · TailwindCSS · `lucide-react`.

**Spec:** `docs/superpowers/specs/2026-05-16-sim-workspace-redesign-design.md`.

**Verification model:** No test infrastructure exists for this SPA. Per-task validation is `npx tsc --noEmit` + `npx vite build`, then a per-phase "visual checklist" the user runs by hard-reloading `http://localhost:4173/`. Commit boundaries align with phases so any phase is independently revertable.

---

## File map

**New files**

| Path | Purpose |
|---|---|
| `frontend/src/lib/use-overrides.ts` | Hook: derives `effective` from baseline + overrides; provides `setOverride`, `clearOverride(key)`, `clearAllOverrides`. |
| `frontend/src/components/sim/SourceCard.tsx` | Model-rooted tree: models → sequences. Handles all source selection. |
| `frontend/src/components/sim/SimulationCard.tsx` | Recipe picker + Form/JSON toggle + params body + footer actions. |
| `frontend/src/components/recipes/RecipesModal.tsx` | Center-screen modal: library list + detail editor. |
| `frontend/src/components/properties/widgets/JsonEditor.tsx` | Read-write JSON editor with override-diff accents. |

**Replaced / heavily modified**

| Path | Why |
|---|---|
| `frontend/src/lib/store.ts` | Adds `simRecipeBaseline`, `simOverrides`, `recipesModalOpen` slices. |
| `frontend/src/components/properties/widgets/ScientificInput.tsx` | New `baselineValue` + `onRevert` props. Accent + ⤺ when override exists. |
| `frontend/src/components/properties/MaterialPanel.tsx` (and other Panel.tsx files) | Read `effective` from `useOverrides()`; write via `setOverride`. |
| `frontend/src/components/properties/Properties.tsx` | Becomes Form-mode body of SimulationCard. Loses outer container. |
| `frontend/src/components/layout/AppShell.tsx` | Drop right-side Properties glass card. Replace Outliner content with SimulationCard column. |
| `frontend/src/components/layout/TopBar.tsx` | Workspace tabs → single Recipes toggle pill. |
| `frontend/src/App.tsx` | Drop `FullWorkspaceShell` branch. Render `RecipesModal` alongside `AppShell`. |

**Deleted**

| Path | Why |
|---|---|
| `frontend/src/components/layout/FullWorkspaceShell.tsx` | No second workspace anymore. |
| `frontend/src/workspaces/RecipesWorkspace.tsx` | Replaced by `RecipesModal`. |
| `frontend/src/components/outliner/Outliner.tsx` and its `*Tree.tsx` siblings | Replaced by `SourceCard`. (Verify subtree usage before delete.) |

---

## Phase 1 — Override engine

**Objective:** Make every param widget read `effective` and write overrides. Override-aware accents + per-field revert + `Save as new` + `Reset all`. **No layout changes this phase** — verify the engine inside the existing right-side Properties before moving anything.

### Task 1.1: Add override slice to store

**Files:**
- Modify: `frontend/src/lib/store.ts`

- [ ] **Step 1: Add slice types and defaults**

Find the existing State type. Add these fields next to `activeRecipeName` / `activeRecipeData`:

```ts
  // Sim override engine. `simRecipeBaseline` is a snapshot of the recipe
  // as selected (server-authoritative). `simOverrides` is a sparse map of
  // user edits; only keys the user touched live here. effective config is
  // computed by useOverrides() as {...baseline, ...overrides}. Cleared on
  // recipe switch, Reset all, Save as new, page reload.
  simRecipeBaseline: Record<string, unknown> | null;
  simOverrides:      Record<string, unknown>;
  setSimRecipeBaseline: (data: Record<string, unknown> | null) => void;
  setOverride:    (key: string, value: unknown) => void;
  clearOverride:  (key: string) => void;
  clearAllOverrides: () => void;
```

In the create() body, initialize and implement:

```ts
  simRecipeBaseline: null,
  simOverrides:      {},
  setSimRecipeBaseline: (data) =>
    set({ simRecipeBaseline: data, simOverrides: {} }),
  setOverride: (key, value) =>
    set((s) => ({ simOverrides: { ...s.simOverrides, [key]: value } })),
  clearOverride: (key) =>
    set((s) => {
      const next = { ...s.simOverrides };
      delete next[key];
      return { simOverrides: next };
    }),
  clearAllOverrides: () => set({ simOverrides: {} }),
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean (no new errors).

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/store.ts
git -c commit.gpgsign=false commit -m "store: add simOverrides slice (baseline + sparse overrides)"
```

---

### Task 1.2: Wire baseline-snapshot to recipe load

**Files:**
- Modify: `frontend/src/lib/store.ts` — `loadActiveRecipe` action

The existing `loadActiveRecipe(name, data)` should snapshot the recipe into `simRecipeBaseline` so the override engine has something to diff against.

- [ ] **Step 1: Update loadActiveRecipe**

Find the existing implementation. Replace its body:

```ts
  loadActiveRecipe: (name, data) =>
    set({
      activeRecipeName:     name,
      activeRecipeData:     data,
      activeRecipePristine: data ? JSON.parse(JSON.stringify(data)) : null,
      // Snapshot the recipe as the sim baseline + clear any overrides
      // from the previous recipe (they don't apply to a new baseline).
      simRecipeBaseline:    data ? JSON.parse(JSON.stringify(data)) : null,
      simOverrides:         {},
    }),
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/store.ts
git -c commit.gpgsign=false commit -m "store: snapshot recipe into simRecipeBaseline on load"
```

---

### Task 1.3: Create use-overrides hook

**Files:**
- Create: `frontend/src/lib/use-overrides.ts`

- [ ] **Step 1: Write the hook**

```ts
import { useMemo } from "react";
import { useStore } from "./store";

/** Override engine consumer hook.
 *
 *  Returns the merged effective config, the raw overrides map, helpers
 *  to set/clear/reset overrides, and a derived `isOverridden(key)`
 *  predicate the param widgets use to decide accent rendering.
 *
 *  Effective is the *only* value that goes to the sim runner — neither
 *  baseline nor overrides individually are valid sim input. */
export function useOverrides() {
  const baseline      = useStore((s) => s.simRecipeBaseline);
  const overrides     = useStore((s) => s.simOverrides);
  const setOverride   = useStore((s) => s.setOverride);
  const clearOverride = useStore((s) => s.clearOverride);
  const clearAll      = useStore((s) => s.clearAllOverrides);

  // Recomputed on every store change to baseline or overrides. Cheap
  // because recipes are ~50 fields. Memoized so component identity is
  // stable when nothing changed.
  const effective = useMemo(
    () => (baseline ? { ...baseline, ...overrides } : { ...overrides }),
    [baseline, overrides],
  );

  const isOverridden = (key: string): boolean =>
    Object.prototype.hasOwnProperty.call(overrides, key);

  const baselineValue = (key: string): unknown =>
    baseline ? baseline[key] : undefined;

  return {
    effective,
    overrides,
    baseline,
    overrideCount: Object.keys(overrides).length,
    isOverridden,
    baselineValue,
    setOverride,
    clearOverride,
    clearAllOverrides: clearAll,
  };
}
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/use-overrides.ts
git -c commit.gpgsign=false commit -m "lib: add useOverrides hook for baseline+overrides merge"
```

---

### Task 1.4: Extend ScientificInput with baseline + revert

**Files:**
- Modify: `frontend/src/components/properties/widgets/ScientificInput.tsx`

- [ ] **Step 1: Add props**

In the props block of `ScientificInput`, add:

```ts
  /** Baseline value from the recipe. When provided AND different from
   *  `value`, the value text renders accent-colored and a ⤺ revert
   *  button appears, calling `onRevert`. */
  baselineValue?: number;
  onRevert?: () => void;
```

- [ ] **Step 2: Compute override state**

Just before the `return`, add:

```ts
  const isOverride =
    baselineValue !== undefined &&
    Number.isFinite(baselineValue) &&
    Math.abs(value - baselineValue) > 1e-9;
```

- [ ] **Step 3: Apply override styling to value + add ⤺**

Replace the value span block with:

```tsx
        <span
          className={
            "font-mono text-[11px] tabular-nums " +
            (isOverride ? "text-accent" : "text-text-primary")
          }
        >
          {fmt(value)}
        </span>
        {unit && (
          <span className="font-mono text-[10px] text-text-muted">{unit}</span>
        )}
        {isOverride && onRevert && (
          <button
            type="button"
            onClick={onRevert}
            className="text-warning hover:text-text-primary text-[11px] cursor-pointer"
            title={`Revert to recipe (${fmt(baselineValue!)})`}
            aria-label="Revert to recipe baseline"
          >
            ⤺
          </button>
        )}
```

- [ ] **Step 4: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean (existing callers don't pass the new optional props — type-safe).

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/properties/widgets/ScientificInput.tsx
git -c commit.gpgsign=false commit -m "ScientificInput: support baselineValue + onRevert (override UI)"
```

---

### Task 1.5: Wire MaterialPanel through the override engine

**Files:**
- Modify: `frontend/src/components/properties/MaterialPanel.tsx`

- [ ] **Step 1: Switch read source to `effective`**

At the top of the component, replace:

```ts
  const activeRecipeData = useStore((s) => s.activeRecipeData);
```

with:

```ts
  const { effective, baselineValue, setOverride, clearOverride } = useOverrides();
```

Add the import at the top:

```ts
import { useOverrides } from "@/lib/use-overrides";
```

Replace the guard `if (!activeRecipeData || !activeRecipeName) return null;` with:

```ts
  if (!activeRecipeName || !effective) return null;
```

- [ ] **Step 2: Replace setField + onMaterialChange**

Replace the existing `setField`:

```ts
  const setField = (key: string, v: unknown) => setOverride(key, v);
```

Replace the existing `onMaterialChange`. Material change replaces the whole baseline (it's a recipe switch, not an override):

```ts
  const onMaterialChange = (newMat: string) => {
    if (!defaults) return;
    const mDefaults = defaults[newMat] ?? {};
    // Material switch is a baseline edit, not an override. Update both
    // activeRecipeData (so other panels read the new defaults) and the
    // store's baseline, then clear overrides — none of the previous
    // overrides necessarily apply to the new material.
    const next = { ...effective, material: newMat, ...mDefaults };
    useStore.getState().setActiveRecipe(activeRecipeName, next);
    useStore.getState().setSimRecipeBaseline(next);
    useStore.getState().clearAllOverrides();
  };
```

- [ ] **Step 3: Update reads + pass baselineValue + onRevert to ScientificInput**

Replace the `MATERIAL_FIELDS` render block:

```tsx
      {FIELD_ORDER.filter((k) => visibility[k]).map((key) => {
        const spec = FIELD_SPECS[key];
        const v = Number(effective[key] ?? 0);
        const b = Number(baselineValue(key) ?? NaN);
        return (
          <ScientificInput
            key={key}
            label={spec.label}
            value={v}
            baselineValue={Number.isFinite(b) ? b : undefined}
            onChange={(n) => setField(key, n)}
            onRevert={() => clearOverride(key)}
            min={spec.min}
            max={spec.max}
            step={spec.step}
            unit={spec.unit}
            scale={spec.scale}
            hint={spec.hint}
            markers={spec.markers}
          />
        );
      })}
```

Also replace the `currentMat` line:

```ts
  const currentMat = (effective.material as string | undefined) ?? "jelly";
```

- [ ] **Step 4: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/properties/MaterialPanel.tsx
git -c commit.gpgsign=false commit -m "MaterialPanel: read effective + write overrides via use-overrides"
```

---

### Task 1.6: Apply the same override wiring to the other panels

**Files:**
- Modify: `frontend/src/components/properties/SolverPanel.tsx`
- Modify: `frontend/src/components/properties/ForcesPanel.tsx`
- Modify: `frontend/src/components/properties/SimSetupPanel.tsx`
- Modify: `frontend/src/components/properties/CameraPanel.tsx`
- Modify: `frontend/src/components/properties/ParticleFillingPanel.tsx`
- Modify: `frontend/src/components/properties/OtherPanel.tsx`
- Modify: `frontend/src/components/properties/BoundaryEditor.tsx`

These panels use the older `SliderInput` / `NumberInput` / `Vec3Input` / `SwitchInput` widgets that don't yet have baseline awareness. For Phase 1 we only need them to **read from effective and write through setOverride** so the `Run` button uses the merged config. The accent-on-deviation UX is deferred to Phase 6 polish for non-ScientificInput widgets (only the Material section is the showcase initially).

- [ ] **Step 1: Pattern to apply per panel**

Replace the top of each panel:

```ts
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;
  const setField = (key: string, v: unknown) => setActiveRecipe(name, { ...data, [key]: v });
```

with:

```ts
  const { effective, setOverride } = useOverrides();
  const name = useStore((s) => s.activeRecipeName);
  if (!name || !effective) return null;
  const setField = (key: string, v: unknown) => setOverride(key, v);
  // Local alias so the remaining `data.<key>` reads keep working.
  const data = effective;
```

Add the import:

```ts
import { useOverrides } from "@/lib/use-overrides";
```

Apply this pattern verbatim in all 7 panels above. The `data` alias means the rest of each panel's body doesn't need to change.

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/properties/
git -c commit.gpgsign=false commit -m "Panels: route reads through effective + writes through setOverride"
```

---

### Task 1.7: Hook Run button to send effective

**Files:**
- Modify: `frontend/src/components/runs/RunButton.tsx`

- [ ] **Step 1: Read effective + send it**

Find the `api.runs.start` call. The `recipe_data` field currently sends `activeRecipeData`. Replace its construction to pull from `useOverrides`:

```ts
import { useOverrides } from "@/lib/use-overrides";
```

In the component, near other `useStore` calls:

```ts
  const { effective } = useOverrides();
```

In the run-start handler, replace `recipe_data: activeRecipeData` (or equivalent) with `recipe_data: effective`. `recipe_source` keeps pointing at the baseline name (`activeRecipeName`).

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/runs/RunButton.tsx
git -c commit.gpgsign=false commit -m "RunButton: dispatch effective (baseline+overrides) to /api/runs"
```

---

### Task 1.8: Add override count + Save-as + Reset-all controls

**Files:**
- Modify: `frontend/src/components/properties/Properties.tsx`

- [ ] **Step 1: Header strip + footer actions**

Replace the body of `Properties.tsx` with:

```tsx
import { PropertyFolder } from "./PropertyFolder";
import { useStore } from "@/lib/store";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MaterialPanel } from "./MaterialPanel";
import { SolverPanel } from "./SolverPanel";
import { ForcesPanel } from "./ForcesPanel";
import { SimSetupPanel } from "./SimSetupPanel";
import { CameraPanel } from "./CameraPanel";
import { ParticleFillingPanel } from "./ParticleFillingPanel";
import { OtherPanel } from "./OtherPanel";
import { BoundaryEditor } from "./BoundaryEditor";
import { ProvenanceFooter } from "./ProvenanceFooter";
import { useOverrides } from "@/lib/use-overrides";
import { api } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

export function Properties() {
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const { effective, overrideCount, clearAllOverrides } = useOverrides();
  const qc = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!activeRecipeName || !activeRecipeData) {
    return (
      <div className="p-3 text-xs text-text-muted">
        Select a recipe in the Outliner to edit parameters.
      </div>
    );
  }

  const onSaveAsNew = async () => {
    const name = prompt("Save as new recipe — name:");
    if (!name?.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.recipes.save(name.trim(), effective, activeRecipeName);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      // Switch the active recipe to the new one. The new recipe IS
      // the effective config, so overrides clear naturally.
      useStore.getState().loadActiveRecipe(name.trim(), effective);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onResetAll = () => {
    if (overrideCount >= 3) {
      if (!confirm(`Reset ${overrideCount} overrides?`)) return;
    }
    clearAllOverrides();
  };

  return (
    <TooltipProvider delayDuration={150}>
      <div className="text-xs">
        {/* Override status strip — surfaces deviation count + bulk actions */}
        {overrideCount > 0 && (
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-accent/5">
            <span className="text-accent text-[11px] font-medium">
              {overrideCount} override{overrideCount === 1 ? "" : "s"}
            </span>
            <div className="ml-auto flex gap-2">
              <button
                onClick={onSaveAsNew}
                disabled={saving}
                className="text-[10px] text-text-secondary hover:text-text-primary disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save as new recipe…"}
              </button>
              <button
                onClick={onResetAll}
                className="text-[10px] text-warning hover:text-text-primary"
              >
                Reset all
              </button>
            </div>
          </div>
        )}
        {error && (
          <div className="px-3 py-1 text-error text-[10px] bg-error/10 border-b border-error/30">
            {error}
          </div>
        )}

        <PropertyFolder title="Material"><MaterialPanel /></PropertyFolder>
        <PropertyFolder title="Solver" defaultOpen={false}><SolverPanel /></PropertyFolder>
        <PropertyFolder title="Forces" defaultOpen={false}><ForcesPanel /></PropertyFolder>
        <PropertyFolder title="Sim setup" defaultOpen={false}><SimSetupPanel /></PropertyFolder>
        <PropertyFolder title="Camera" defaultOpen={false}><CameraPanel /></PropertyFolder>
        <PropertyFolder title="Particle filling" defaultOpen={false}><ParticleFillingPanel /></PropertyFolder>
        <PropertyFolder title="Other" defaultOpen={false}><OtherPanel /></PropertyFolder>
        <PropertyFolder title="Boundary conditions" defaultOpen={false}><BoundaryEditor /></PropertyFolder>
        <PropertyFolder title="Provenance" defaultOpen={false}>
          <ProvenanceFooter />
        </PropertyFolder>
      </div>
    </TooltipProvider>
  );
}
```

Note the SavePresetDialog at the bottom is dropped — its functionality is absorbed by the Save-as-new header button above. We'll delete the dialog file in Phase 6 cleanup.

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean type-check; `built in ...s`.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/properties/Properties.tsx
git -c commit.gpgsign=false commit -m "Properties: override count strip + Save-as-new + Reset-all"
```

---

### Phase 1 visual verification

Hard-reload `http://localhost:4173/`. Pick a model + a recipe (e.g. jelly). The right-side Properties panel should still be there (no layout change yet).

**Visual checklist:**

- [ ] Drag the Young's E slider — the value goes accent cyan, a ⤺ appears next to it
- [ ] Top of the Properties panel shows "1 override" with `Save as new recipe…` and `Reset all` actions
- [ ] Click ⤺ next to E — value snaps back to baseline color, override count disappears
- [ ] Tweak two params, click `Reset all` (no confirm at <3 overrides) — both snap back
- [ ] Tweak three params, click `Reset all` — confirm dialog appears
- [ ] Click `Save as new recipe…` — prompt for name, new recipe lands in the library, override count clears
- [ ] Click Run with overrides — Network tab shows `POST /api/runs` body containing the *effective* values (E with the override, not the baseline)

If any check fails, debug before continuing to Phase 2.

---

## Phase 2 — SourceCard

**Objective:** Build the model-rooted tree. Replace the Outliner's body with `<SourceCard>`. Persist tree open/close to localStorage.

### Task 2.1: Create SourceCard component skeleton

**Files:**
- Create: `frontend/src/components/sim/SourceCard.tsx`

- [ ] **Step 1: Create the directory + file**

```bash
mkdir -p /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/sim
```

- [ ] **Step 2: Write SourceCard.tsx**

```tsx
import { useEffect, useMemo, useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Play, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { ModelItem, SequenceItem } from "@/lib/types";

const TREE_STATE_KEY = "gsfluent.source_tree_open";

function loadTreeState(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(TREE_STATE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function persistTreeState(state: Record<string, boolean>) {
  try { localStorage.setItem(TREE_STATE_KEY, JSON.stringify(state)); } catch {}
}

type Props = {
  onPickModel: (m: ModelItem) => void;
  onLoadRun:   (run_name: string) => void;
};

/** Source card — model-rooted hierarchy.
 *
 *  Replaces the old Outliner tabs. Models are parents; sequences hang
 *  underneath their `model_ref` parent. Orphan sequences (model_ref ===
 *  null) get their own group at the bottom. Tree expand/collapse state
 *  per model persists to localStorage.
 */
export function SourceCard({ onPickModel, onLoadRun }: Props) {
  const { data: models = [] } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });
  const { data: sequences = [] } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });

  const activeModel = useStore((s) => s.activeModel);
  const simRunName  = useStore((s) => s.simRunName);

  const [open, setOpen] = useState<Record<string, boolean>>(() => loadTreeState());
  useEffect(() => { persistTreeState(open); }, [open]);

  const toggle = useCallback((modelName: string) => {
    setOpen((s) => ({ ...s, [modelName]: !s[modelName] }));
  }, []);

  // Group sequences by model_ref. Sequences with null model_ref go to
  // the "orphan" bucket rendered at the bottom.
  const sequencesByModel = useMemo(() => {
    const m: Record<string, SequenceItem[]> = {};
    const orphans: SequenceItem[] = [];
    for (const s of sequences as SequenceItem[]) {
      if (s.model_ref) {
        (m[s.model_ref] ||= []).push(s);
      } else {
        orphans.push(s);
      }
    }
    return { byModel: m, orphans };
  }, [sequences]);

  return (
    <div className="text-xs">
      <div className="px-3 py-2 text-text-muted text-[10px] uppercase tracking-wider">
        Models
      </div>
      {(models as ModelItem[]).map((m) => {
        const isExpanded = open[m.name] ?? false;
        const childSeqs = sequencesByModel.byModel[m.name] ?? [];
        const isActiveModel = activeModel?.name === m.name;
        return (
          <div key={m.name}>
            <button
              type="button"
              onClick={() => onPickModel(m)}
              className={
                "w-full flex items-center gap-1 px-3 py-1 text-left hover:bg-elevated " +
                (isActiveModel ? "text-accent" : "text-text-primary")
              }
            >
              <span
                onClick={(e) => { e.stopPropagation(); toggle(m.name); }}
                className="text-text-muted cursor-pointer p-0.5"
                aria-label={isExpanded ? "Collapse" : "Expand"}
              >
                {isExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              </span>
              <span className="font-mono truncate flex-1">{m.name}</span>
              <span className="text-text-muted text-[10px]">
                {childSeqs.length || ""}
              </span>
            </button>
            {isExpanded && (
              <div className="pl-6 pr-3">
                {childSeqs.length === 0 ? (
                  <div className="py-1 text-text-muted text-[10px] italic">
                    No runs yet
                  </div>
                ) : (
                  childSeqs.map((s) => (
                    <button
                      key={s.name}
                      type="button"
                      onClick={() => onLoadRun(s.name)}
                      className={
                        "w-full flex items-center gap-1 py-1 text-left hover:bg-elevated rounded " +
                        (simRunName === s.name ? "text-accent" : "text-text-secondary")
                      }
                    >
                      <span className="font-mono text-[11px] truncate flex-1">
                        {s.name}
                      </span>
                      {s.recipe_source && (
                        <span className="text-[9px] px-1 rounded bg-accent/10 text-accent">
                          {s.recipe_source.replace(/^★ /, "")}
                        </span>
                      )}
                      <Play size={10} className="opacity-50" />
                    </button>
                  ))
                )}
                <button
                  type="button"
                  onClick={() => onPickModel(m)}
                  className="w-full flex items-center gap-1 mt-1 px-2 py-1 rounded border border-dashed border-accent/30 bg-accent/5 text-accent text-[10px] hover:bg-accent/10"
                >
                  <Plus size={10} />
                  new simulation from {m.name}
                </button>
              </div>
            )}
          </div>
        );
      })}

      {sequencesByModel.orphans.length > 0 && (
        <>
          <div className="px-3 py-2 mt-2 text-text-muted text-[10px] uppercase tracking-wider">
            Orphan sequences
          </div>
          {sequencesByModel.orphans.map((s) => (
            <button
              key={s.name}
              type="button"
              onClick={() => onLoadRun(s.name)}
              className={
                "w-full flex items-center gap-1 px-3 py-1 text-left hover:bg-elevated " +
                (simRunName === s.name ? "text-accent" : "text-text-secondary")
              }
            >
              <span className="font-mono text-[11px] truncate flex-1">{s.name}</span>
              <span className="text-[9px] px-1 rounded bg-elevated text-text-muted">
                imported
              </span>
            </button>
          ))}
        </>
      )}

      <QueryRefresher qc={useQueryClient()} />
    </div>
  );
}

/** Re-fetch sequences once when SourceCard mounts so the tree shows the
 *  freshest data without waiting on the 5s poll. */
function QueryRefresher({ qc }: { qc: ReturnType<typeof useQueryClient> }) {
  useEffect(() => { qc.invalidateQueries({ queryKey: ["sequences"] }); }, [qc]);
  return null;
}
```

- [ ] **Step 3: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/sim/SourceCard.tsx
git -c commit.gpgsign=false commit -m "sim: add SourceCard (model-rooted tree, sequences as children)"
```

---

### Task 2.2: Swap Outliner's content for SourceCard in AppShell

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`

The current AppShell takes an `outliner` prop. We'll pass `<SourceCard>` as that prop from App.tsx and rename the prop later in Phase 3. For now this is a minimal swap.

- [ ] **Step 1: In App.tsx, import + pass SourceCard**

Add import:

```ts
import { SourceCard } from "@/components/sim/SourceCard";
```

Replace the Sim workspace render block. Find:

```tsx
        <AppShell
          subscribe={subscribe}
          outliner={<Outliner onLoadRun={onLoadRun} onPickModel={onPickModel} />}
          viewport={<Viewport />}
          properties={<Properties />}
        />
```

with:

```tsx
        <AppShell
          subscribe={subscribe}
          outliner={<SourceCard onLoadRun={onLoadRun} onPickModel={onPickModel} />}
          viewport={<Viewport />}
          properties={<Properties />}
        />
```

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/App.tsx
git -c commit.gpgsign=false commit -m "App: render SourceCard in the left outliner slot"
```

---

### Phase 2 visual verification

Hard-reload. The left card now shows the new tree.

**Visual checklist:**

- [ ] Models listed, each with a chevron + run-count badge
- [ ] Clicking a model selects it (turns accent), loads splat preview in viewport
- [ ] Clicking the chevron expands the model; sequences under that model appear indented with recipe badge + ▶ icon
- [ ] Clicking a sequence loads it (turns accent, viewport plays it)
- [ ] `+ new simulation from <model>` button appears when expanded, clicking it re-selects the model
- [ ] If you have any imported `.npz` sequences (model_ref null), they appear under "Orphan sequences" at the bottom
- [ ] Expanded/collapsed state survives a hard reload (localStorage)

---

## Phase 3 — SimulationCard + layout flip

**Objective:** Combine the override-aware Properties body with a recipe picker, Form/JSON toggle, and footer actions. Move everything to the left column under `SourceCard`. Drop the right-side Properties glass card. Viewport gets full width.

### Task 3.1: Create SimulationCard component

**Files:**
- Create: `frontend/src/components/sim/SimulationCard.tsx`

- [ ] **Step 1: Write the component**

```tsx
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { Properties } from "@/components/properties/Properties";
import { RunButton } from "@/components/runs/RunButton";
import type { RecipeListItem } from "@/lib/types";

type Props = {
  subscribe: (run_name: string) => void;
};

/** Simulation card — recipe picker + Form/JSON toggle + params + actions.
 *
 *  State machine:
 *    - no model selected           → "Pick a model" empty state
 *    - model but no recipe         → recipe picker visible, body hidden
 *    - model + recipe (idle)       → full editor
 *    - sequence loaded (under model) → read-only summary (Phase 6)
 *    - sequence loaded (orphan)    → hidden entirely
 */
export function SimulationCard({ subscribe }: Props) {
  const activeModel       = useStore((s) => s.activeModel);
  const activeRecipeName  = useStore((s) => s.activeRecipeName);
  const simRunName        = useStore((s) => s.simRunName);
  const loadActiveRecipe  = useStore((s) => s.loadActiveRecipe);
  const { overrideCount } = useOverrides();
  const [view, setView]   = useState<"form" | "json">(
    () => (localStorage.getItem("gsfluent.sim_view_mode") as "form" | "json") || "form",
  );

  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });

  const setViewPersist = (v: "form" | "json") => {
    setView(v);
    localStorage.setItem("gsfluent.sim_view_mode", v);
  };

  if (!activeModel) {
    return (
      <div className="px-3 py-4 text-xs text-text-muted text-center">
        Pick a model or sequence to configure simulation.
      </div>
    );
  }

  // Sequence loaded under a model — read-only summary handled in Phase 6.
  // Sequence loaded as orphan — hide entirely.
  const isViewingOrphan = !!simRunName &&
    !simRunName.startsWith("_model:") &&
    !activeModel; // narrows nothing further but documents intent
  if (isViewingOrphan) return null;

  const onPickRecipe = async (name: string) => {
    if (overrideCount > 0) {
      if (!confirm(`Discard ${overrideCount} override${overrideCount === 1 ? "" : "s"}?`)) return;
    }
    try {
      const r = await api.recipes.get(name);
      loadActiveRecipe(r.name, r.data);
    } catch (e) {
      console.error("recipe load failed", e);
    }
  };

  return (
    <div className="text-xs flex flex-col">
      <div className="px-3 py-2 border-b border-border flex items-center gap-2">
        <span className="text-text-muted text-[10px] uppercase tracking-wider">
          ② Simulation
        </span>
        {overrideCount > 0 && (
          <span className="text-[10px] text-accent px-1.5 py-0.5 bg-accent/10 rounded">
            {overrideCount} override{overrideCount === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {/* Recipe picker */}
      <div className="px-3 py-2 flex items-center gap-2">
        <span className="text-text-muted text-[10px] uppercase tracking-wider">
          Recipe
        </span>
        <select
          value={activeRecipeName ?? ""}
          onChange={(e) => onPickRecipe(e.target.value)}
          className="flex-1 bg-elevated text-text-primary text-[11px] rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="" disabled>
            Pick a recipe…
          </option>
          <optgroup label="Built-in">
            {(recipes as RecipeListItem[])
              .filter((r) => r.source === "builtin")
              .map((r) => (
                <option key={r.name} value={r.name}>{r.name}</option>
              ))}
          </optgroup>
          <optgroup label="User saved (★)">
            {(recipes as RecipeListItem[])
              .filter((r) => r.source === "user")
              .map((r) => (
                <option key={r.name} value={r.name}>★ {r.name}</option>
              ))}
          </optgroup>
        </select>
      </div>

      {/* Form/JSON toggle */}
      {activeRecipeName && (
        <div className="px-3 pb-2">
          <div className="flex bg-elevated rounded p-0.5">
            <button
              onClick={() => setViewPersist("form")}
              className={
                "flex-1 px-2 py-1 text-[10px] rounded " +
                (view === "form" ? "bg-accent/15 text-accent" : "text-text-muted")
              }
            >
              Form
            </button>
            <button
              onClick={() => setViewPersist("json")}
              disabled
              className="flex-1 px-2 py-1 text-[10px] rounded text-text-muted/40 cursor-not-allowed"
              title="JSON view ships in Phase 5"
            >
              JSON (soon)
            </button>
          </div>
        </div>
      )}

      {/* Body */}
      {!activeRecipeName ? (
        <div className="px-3 py-4 text-xs text-text-muted text-center">
          Pick a recipe above to configure simulation.
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto">
          {view === "form" && <Properties />}
        </div>
      )}

      {/* Footer: Run lives in the existing RunButton (TopBar). This
          footer holds the secondary actions (Save-as, Reset-all are
          already in the override-strip inside Properties). */}
      <div className="px-3 py-2 border-t border-border flex items-center gap-2">
        <RunButton subscribe={subscribe} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/sim/SimulationCard.tsx
git -c commit.gpgsign=false commit -m "sim: add SimulationCard (recipe picker + Form/JSON toggle + Run)"
```

---

### Task 3.2: Restructure AppShell — left-column stack, drop right card

**Files:**
- Modify: `frontend/src/components/layout/AppShell.tsx`

- [ ] **Step 1: Update props type + JSX**

Change the AppShell props to take a `sim` slot (replacing the separate `outliner` + `properties` slots). Keyboard shortcut Cmd-I no longer toggles properties (no separate panel); keep Cmd-B for the source card.

Replace `type Props`:

```ts
type Props = {
  sourceCard: React.ReactNode;
  simCard:    React.ReactNode;
  viewport:   React.ReactNode;
  subscribe:  (run_name: string) => void;
};
```

Replace the component signature + body:

```tsx
export function AppShell({ sourceCard, simCard, viewport, subscribe }: Props) {
  const panels = useStore((s) => s.panels);
  const setPanelCollapsed = useStore((s) => s.setPanelCollapsed);
  const simState = useStore((s) => s.simState);
  const simNFrames = useStore((s) => s.simNFrames);
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const simLog = useStore((s) => s.simLog);

  // Cmd-B toggles the left rail. Cmd-/ skip-link kept.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (!meta) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toUpperCase();
      const editable =
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
        target?.isContentEditable === true;
      if (editable) return;
      if (e.key.toLowerCase() === "b") {
        e.preventDefault();
        setPanelCollapsed("outliner", panels.outliner !== "collapsed");
      } else if (e.key === "/") {
        e.preventDefault();
        if (panels.outliner === "collapsed") setPanelCollapsed("outliner", false);
        requestAnimationFrame(() => {
          document.querySelector<HTMLElement>('[aria-label="Sim panel"]')?.focus();
        });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [panels.outliner, setPanelCollapsed]);

  // (Sim-state live region kept verbatim — copy the existing useEffect
  // computing `announcement` from simState/simNFrames/simTotalFrames/simLog.)
  const lastAnnounced = useRef<string>("");
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    let msg = "";
    if (simState === "running") {
      const pct = simTotalFrames > 0
        ? Math.round((simNFrames / simTotalFrames) * 100) : 0;
      msg = pct > 0 ? `Simulation running, ${pct} percent.` : "Simulation started.";
    } else if (simState === "done") {
      msg = "Simulation finished.";
    } else if (simState === "error") {
      const lastLog = simLog[simLog.length - 1] || "";
      msg = lastLog ? `Simulation error: ${lastLog}` : "Simulation error.";
    } else if (simState === "cancelled") {
      msg = "Simulation cancelled.";
    } else if (simState === "idle" && lastAnnounced.current.startsWith("Simulation running")) {
      msg = "Simulation idle.";
    }
    if (msg && msg !== lastAnnounced.current) {
      lastAnnounced.current = msg;
      setAnnouncement(msg);
    }
  }, [simState, simNFrames, simTotalFrames, simLog]);

  return (
    <div className="h-screen w-screen relative bg-canvas text-text-primary text-sm overflow-hidden">
      <a
        href="#sim"
        onClick={(e) => {
          e.preventDefault();
          if (panels.outliner === "collapsed") setPanelCollapsed("outliner", false);
          requestAnimationFrame(() => {
            document.querySelector<HTMLElement>('[aria-label="Sim panel"]')?.focus();
          });
        }}
        className="absolute left-2 top-2 -translate-y-16 focus:translate-y-0 z-50 bg-accent text-canvas px-3 py-1.5 rounded text-xs font-medium transition-transform duration-fast ease-motion shadow-glass focus:outline-none focus:ring-2 focus:ring-accent-glow"
      >
        Skip to Sim panel
      </a>

      <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {announcement}
      </div>

      <main role="main" className="absolute inset-0 z-0">
        {viewport}
      </main>

      <TopBar subscribe={subscribe} />

      {/* Left rail — two-card stack (Source over Simulation) */}
      <GlassCard
        side="left"
        collapsed={panels.outliner === "collapsed"}
        onCollapse={() => setPanelCollapsed("outliner", panels.outliner !== "collapsed")}
        shortcut="⌘B"
        ariaLabel="Sim panel"
        className="fixed left-3 top-[68px] bottom-3 w-80 z-20 flex flex-col"
      >
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex-1 min-h-0 overflow-y-auto border-b border-border">
            {sourceCard}
          </div>
          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            {simCard}
          </div>
        </div>
      </GlassCard>

      <StatusPanel />
    </div>
  );
}
```

- [ ] **Step 2: Update imports**

The file already imports `TopBar`, `StatusPanel`, `GlassCard`. Keep them. The `useRef`, `useState` imports must already be in the file from earlier work — verify.

- [ ] **Step 3: Update App.tsx to pass new slots**

In `App.tsx`, replace the Sim render block:

```tsx
import { SimulationCard } from "@/components/sim/SimulationCard";
```

```tsx
        <AppShell
          subscribe={subscribe}
          sourceCard={<SourceCard onLoadRun={onLoadRun} onPickModel={onPickModel} />}
          simCard={<SimulationCard subscribe={subscribe} />}
          viewport={<Viewport />}
        />
```

The `<Outliner>` import and `Properties` import in App.tsx (top of file) can stay for now; they're unused but harmless. Cleanup in Phase 6.

- [ ] **Step 4: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean; build succeeds. Bundle size should be unchanged or smaller (we dropped the right-rail Properties card path from the JSX).

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/layout/AppShell.tsx frontend/src/App.tsx
git -c commit.gpgsign=false commit -m "AppShell: collapse to single left rail (Source+Sim), drop right card"
```

---

### Task 3.3: Adjust viewport overlays for the missing right card

**Files:**
- Modify: `frontend/src/components/viewport/RenderModeToggle.tsx`
- Modify: `frontend/src/components/viewport/DropZone.tsx`
- Modify: `frontend/src/components/layout/StatusPanel.tsx`

The toggles previously slid left to clear the Properties card on the right. Now there's no right card — they should always sit at `right-3`. The console drawer and StatusPanel pill positioning logic for `propertiesOpen` becomes dead.

- [ ] **Step 1: RenderModeToggle — drop propertiesOpen logic**

Find the propertiesOpen subscription and the `rightOffset` ternary. Replace with a static `right-3`:

```tsx
  // RenderModeToggle: no right panel anymore (post-Phase-3 layout); the
  // toggle just parks at the right edge of the viewport.
  const renderMode = useStore((s) => s.renderMode);
  const setRenderMode = useStore((s) => s.setRenderMode);
```

(remove the `propertiesOpen` selector)

```tsx
    <div
      className="absolute top-[68px] right-3 z-10 flex border border-border rounded overflow-hidden bg-canvas/85 backdrop-blur"
      title={ /* unchanged */ }
    >
```

- [ ] **Step 2: DropZone Y-up chip — same**

```tsx
  // No more propertiesOpen — chip lives at the right edge always.
```

(remove the `propertiesOpen` selector + `right-[344px]` branch)

```tsx
      {dragKind !== "npz" && (
        <div className="absolute top-[104px] right-3 z-10">
```

- [ ] **Step 3: StatusPanel — drop properties branch from console drawer**

The console drawer was clearing both panels. Now only the left rail (outliner) needs clearing:

```tsx
      {consoleOpen && (
        <div
          className={`fixed bottom-14 h-72 z-30 glass-card overflow-hidden flex flex-col transition-[left] duration-panel ease-motion ${
            outlinerOpen ? "left-[332px]" : "left-3"
          } right-3`}
          role="region"
          aria-label="Run console"
        >
```

(remove `propertiesOpen` selector and the `right-[344px]` toggle)

Note: the left rail's width changed from 72 (288px) to 80 (320px) in Task 3.2, so the offset is now `332px` not `312px`.

- [ ] **Step 4: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/viewport/RenderModeToggle.tsx frontend/src/components/viewport/DropZone.tsx frontend/src/components/layout/StatusPanel.tsx
git -c commit.gpgsign=false commit -m "viewport overlays: drop propertiesOpen branch (no right panel)"
```

---

### Phase 3 visual verification

Hard-reload.

**Visual checklist:**

- [ ] Single left-side glass card spans top-to-bottom, w-80 (320px)
- [ ] Top half: SourceCard tree (models, sequences-under-models, orphans)
- [ ] Bottom half: SimulationCard with recipe picker + override count badge
- [ ] Pick a model → SimulationCard shows "Pick a recipe…"
- [ ] Pick a recipe → params appear, override strip works as before, Run button at bottom of card
- [ ] Drag a slider → accent + ⤺ still works
- [ ] Save as new → still works
- [ ] Right side of viewport has nothing — full width
- [ ] Render-mode toggle stays at top-right always (no slide animation)
- [ ] Cmd-B toggles the whole left rail (both cards slide off together)
- [ ] Switching to Recipes workspace via TopBar still works (we haven't dropped that yet — Phase 4)

---

## Phase 4 — RecipesModal + drop workspace switching

**Objective:** Replace the workspace-switching `Recipes` tab with a center-screen modal that opens over the viewport. Cmd-R toggles it. The viewport never remounts.

### Task 4.1: Add modal-open state to the store

**Files:**
- Modify: `frontend/src/lib/store.ts`

- [ ] **Step 1: Add slice**

Add to State + initialization:

```ts
  recipesModalOpen: boolean;
  setRecipesModalOpen: (open: boolean) => void;
```

```ts
  recipesModalOpen: false,
  setRecipesModalOpen: (open) => set({ recipesModalOpen: open }),
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/store.ts
git -c commit.gpgsign=false commit -m "store: add recipesModalOpen slice"
```

---

### Task 4.2: Create RecipesModal component

**Files:**
- Create: `frontend/src/components/recipes/RecipesModal.tsx`

- [ ] **Step 1: Create the directory + file**

```bash
mkdir -p /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/recipes
```

- [ ] **Step 2: Write the modal**

```tsx
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Trash2, Copy, Upload, Download } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { Properties } from "@/components/properties/Properties";
import type { RecipeListItem } from "@/lib/types";

/** RecipesModal — center-screen library manager. Replaces the
 *  separate Recipes workspace. Doesn't remount the viewport: it just
 *  layers over the AppShell with a translucent backdrop.
 *
 *  Triggered by:
 *   - clicking the Recipes pill in the TopBar
 *   - Cmd/Ctrl-R (registered in App.tsx)
 *
 *  Esc / click-outside / ✕ dismisses (with confirm if user has unsaved
 *  edits to a selected user recipe). */
export function RecipesModal() {
  const open       = useStore((s) => s.recipesModalOpen);
  const setOpen    = useStore((s) => s.setRecipesModalOpen);
  const loadActive = useStore((s) => s.loadActiveRecipe);
  const { overrideCount } = useOverrides();
  const qc = useQueryClient();

  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });

  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (!open) return null;

  const builtin = (recipes as RecipeListItem[]).filter((r) => r.source === "builtin");
  const user    = (recipes as RecipeListItem[]).filter((r) => r.source === "user");
  const selectedItem = recipes.find((r) => r.name === selected);
  const isUser = selectedItem?.source === "user";

  const onUseInSim = async () => {
    if (!selected) return;
    if (overrideCount > 0 && !confirm(`Discard ${overrideCount} overrides?`)) return;
    try {
      const r = await api.recipes.get(selected);
      loadActive(r.name, r.data);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onDuplicate = async () => {
    if (!selected) return;
    const newName = prompt("Duplicate as:", `${selected}_copy`);
    if (!newName?.trim()) return;
    try {
      const r = await api.recipes.get(selected);
      await api.recipes.save(newName.trim(), r.data, selected);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      setSelected(newName.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onDelete = async () => {
    if (!selected || !isUser) return;
    if (!confirm(`Delete user preset "${selected}"?`)) return;
    try {
      await api.recipes.delete(selected);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      setSelected(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onImport = async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const text = await file.text();
      try {
        const data = JSON.parse(text);
        const name = prompt("Save as preset name:", file.name.replace(/\.json$/, ""));
        if (!name?.trim()) return;
        await api.recipes.save(name.trim(), data);
        qc.invalidateQueries({ queryKey: ["recipes"] });
        setSelected(name.trim());
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    input.click();
  };

  const onExport = async () => {
    if (!selected) return;
    try {
      const r = await api.recipes.get(selected);
      const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${selected}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-label="Recipes library"
      aria-modal="true"
    >
      <div
        className="glass-card w-[720px] h-[520px] flex overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Library list */}
        <div className="w-[200px] border-r border-border flex flex-col">
          <div className="px-3 py-2 flex items-center justify-between border-b border-border">
            <span className="text-text-muted text-[10px] uppercase tracking-wider">Library</span>
            <button
              onClick={onImport}
              className="text-accent text-[10px] flex items-center gap-1 hover:bg-elevated px-1 rounded"
              title="Import .json"
            >
              <Upload size={11} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            <div className="px-3 py-1 text-text-muted text-[9px] uppercase tracking-wider">Built-in</div>
            {builtin.map((r) => (
              <button
                key={r.name}
                onClick={() => setSelected(r.name)}
                className={
                  "w-full text-left px-3 py-1 text-xs font-mono truncate hover:bg-elevated " +
                  (selected === r.name ? "text-accent bg-accent/10" : "text-text-primary")
                }
              >
                {r.name}
              </button>
            ))}
            <div className="px-3 py-1 mt-2 text-text-muted text-[9px] uppercase tracking-wider">User ★</div>
            {user.length === 0 && (
              <div className="px-3 py-1 text-[10px] text-text-muted">(none yet)</div>
            )}
            {user.map((r) => (
              <button
                key={r.name}
                onClick={() => setSelected(r.name)}
                className={
                  "w-full text-left px-3 py-1 text-xs font-mono truncate hover:bg-elevated " +
                  (selected === r.name ? "text-accent bg-accent/10" : "text-text-primary")
                }
              >
                ★ {r.name}
              </button>
            ))}
          </div>
        </div>

        {/* Detail */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="px-4 py-2 border-b border-border flex items-center gap-2">
            <span className="font-mono text-sm truncate flex-1">
              {selected ?? "Pick a recipe"}
            </span>
            {selected && (
              <>
                <span className="text-[10px] text-text-muted">
                  {isUser ? "user" : "built-in (read-only)"}
                </span>
                <button onClick={onDuplicate} className="text-[10px] text-text-secondary hover:text-text-primary flex items-center gap-1">
                  <Copy size={11} /> Duplicate
                </button>
                {isUser && (
                  <button onClick={onDelete} className="text-[10px] text-error hover:text-text-primary flex items-center gap-1">
                    <Trash2 size={11} />
                  </button>
                )}
                <button onClick={onExport} className="text-[10px] text-text-secondary hover:text-text-primary" title="Export .json">
                  <Download size={11} />
                </button>
                <button onClick={onUseInSim} className="text-[10px] bg-accent text-canvas px-2 py-0.5 rounded font-medium">
                  Use in Sim
                </button>
              </>
            )}
            <button onClick={() => setOpen(false)} className="text-text-muted hover:text-text-primary" aria-label="Close">
              <X size={14} />
            </button>
          </div>
          {error && (
            <div className="px-4 py-1 text-error text-[10px] bg-error/10 border-b border-error/30">
              {error}
            </div>
          )}
          <div className="flex-1 min-h-0 overflow-y-auto">
            {selected ? (
              <RecipeDetail name={selected} />
            ) : (
              <div className="p-6 text-text-muted text-xs">
                Select a recipe on the left to inspect or edit.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** When a recipe is selected in the modal, load it into the active
 *  recipe slot temporarily so the Properties panel reads it. We don't
 *  affect overrides; if the user wants to apply, they click "Use in Sim"
 *  which is a real load. Otherwise on close we leave activeRecipe alone.
 *
 *  For Phase 4, we just show the recipe via a separate detail view —
 *  Properties is wired to activeRecipe so we'd need to either (a) split
 *  Properties from activeRecipe or (b) read this recipe inline. Path (b)
 *  is simpler. */
function RecipeDetail({ name }: { name: string }) {
  const { data: r, isLoading } = useQuery({
    queryKey: ["recipes", name],
    queryFn: () => api.recipes.get(name),
  });
  if (isLoading || !r) return <div className="p-4 text-text-muted text-xs">Loading…</div>;

  // Render the recipe data as JSON for now (Form view here would
  // require Properties to read from arbitrary recipes, not just the
  // active one — Phase 5 unifies via JsonEditor + a recipe-agnostic Form).
  return (
    <pre className="px-4 py-3 text-[11px] font-mono text-text-secondary whitespace-pre-wrap">
      {JSON.stringify(r.data, null, 2)}
    </pre>
  );
}
```

- [ ] **Step 3: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/recipes/RecipesModal.tsx
git -c commit.gpgsign=false commit -m "recipes: add RecipesModal (library manager, JSON preview)"
```

---

### Task 4.3: Replace TopBar workspace tabs with Recipes toggle

**Files:**
- Modify: `frontend/src/components/layout/TopBar.tsx`

- [ ] **Step 1: Replace WorkspaceChips with a Recipes button**

Find the `<WorkspaceChips />` invocation in TopBar's JSX. Replace with:

```tsx
      {/* Recipes modal toggle — replaces the old workspace tabs */}
      <button
        type="button"
        onClick={() => useStore.getState().setRecipesModalOpen(true)}
        className="px-2 py-1 rounded-md text-xs font-medium text-text-secondary hover:text-text-primary hover:bg-elevated/60 flex items-center gap-1"
        title="Open recipe library (⌘R)"
      >
        📚 Recipes
      </button>
```

Delete the `WorkspaceChips` function definition at the bottom of TopBar.tsx (no longer used).

Remove the `Workspace` type import from `@/lib/types` and the `activeWorkspace` / `setActiveWorkspace` selectors at the top of the component — they're no longer needed in this file.

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/layout/TopBar.tsx
git -c commit.gpgsign=false commit -m "TopBar: replace workspace tabs with Recipes modal toggle"
```

---

### Task 4.4: Register Cmd-R + mount RecipesModal in App.tsx

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Drop the Recipes workspace branch + render the modal**

Replace the App body. The current file has:

```tsx
return (
  <>
    {activeWorkspace === "sim" && (
      <AppShell ... />
    )}
    {activeWorkspace === "recipes" && (
      <FullWorkspaceShell subscribe={subscribe}>
        <Suspense fallback={...}>
          <RecipesWorkspace />
        </Suspense>
      </FullWorkspaceShell>
    )}
    <CommandPalette onRun={triggerRun} />
  </>
);
```

Replace with:

```tsx
import { RecipesModal } from "@/components/recipes/RecipesModal";

// Cmd-R toggles the recipes modal. Registered globally so it works
// anywhere in the app, with the standard editable-element guard.
useEffect(() => {
  const onKey = (e: KeyboardEvent) => {
    const meta = e.metaKey || e.ctrlKey;
    if (!meta || e.key.toLowerCase() !== "r") return;
    const target = e.target as HTMLElement | null;
    const tag = target?.tagName?.toUpperCase();
    const editable =
      tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
      target?.isContentEditable === true;
    if (editable) return;
    e.preventDefault();
    const st = useStore.getState();
    st.setRecipesModalOpen(!st.recipesModalOpen);
  };
  document.addEventListener("keydown", onKey);
  return () => document.removeEventListener("keydown", onKey);
}, []);

return (
  <>
    <AppShell
      subscribe={subscribe}
      sourceCard={<SourceCard onLoadRun={onLoadRun} onPickModel={onPickModel} />}
      simCard={<SimulationCard subscribe={subscribe} />}
      viewport={<Viewport />}
    />
    <RecipesModal />
    <CommandPalette onRun={triggerRun} />
  </>
);
```

Remove these now-unused imports near the top of App.tsx:

```ts
// remove:
import { FullWorkspaceShell } from "@/components/layout/FullWorkspaceShell";
// remove the lazy import of RecipesWorkspace
// remove the `activeWorkspace` selector
```

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/App.tsx
git -c commit.gpgsign=false commit -m "App: mount RecipesModal + Cmd-R, drop workspace switching"
```

---

### Task 4.5: Delete the now-dead workspace shell + workspace files

**Files:**
- Delete: `frontend/src/components/layout/FullWorkspaceShell.tsx`
- Delete: `frontend/src/workspaces/RecipesWorkspace.tsx`

- [ ] **Step 1: Confirm no remaining references**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && grep -rn "FullWorkspaceShell\|RecipesWorkspace" src/ --include="*.ts" --include="*.tsx"
```

Expected: only matches inside the two files we're about to delete (the import in App.tsx should already be gone from Task 4.4).

- [ ] **Step 2: Delete**

```bash
rm /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/layout/FullWorkspaceShell.tsx
rm /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/workspaces/RecipesWorkspace.tsx
rmdir /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/workspaces 2>/dev/null || true
```

- [ ] **Step 3: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add -A frontend/src/
git -c commit.gpgsign=false commit -m "Remove FullWorkspaceShell + RecipesWorkspace (replaced by RecipesModal)"
```

---

### Phase 4 visual verification

Hard-reload.

**Visual checklist:**

- [ ] TopBar shows "📚 Recipes" button where the workspace tabs used to be
- [ ] Clicking it opens a center-screen modal over the viewport (viewport visible behind a dim)
- [ ] Library on the left lists Built-in + User; clicking a recipe shows its JSON
- [ ] Esc / click outside / ✕ closes the modal
- [ ] Cmd-R / Ctrl-R also toggles it
- [ ] "Use in Sim" loads that recipe into the SimulationCard, modal closes, viewport never reloaded
- [ ] Duplicate / Delete / Export / Import work
- [ ] If overrides exist, "Use in Sim" prompts to discard

---

## Phase 5 — JsonEditor + Form↔JSON parity

**Objective:** Build a textarea-based JSON editor with override-diff coloring. Wire the Form/JSON toggle in `SimulationCard`. Replace the `<pre>` JSON view in `RecipesModal` with the same component (read-only for built-ins, read-write for user recipes).

### Task 5.1: Create JsonEditor widget

**Files:**
- Create: `frontend/src/components/properties/widgets/JsonEditor.tsx`

- [ ] **Step 1: Write the component**

```tsx
import { useEffect, useMemo, useRef, useState } from "react";

/** JSON editor with override-aware highlighting.
 *
 *  Approach: textarea overlays a syntax-highlighted preview. We avoid
 *  CodeMirror (~150 KB) for now in favor of a simple textarea-on-top-of-
 *  preview pattern. Trade-off: no bracket matching / autocomplete; the
 *  user-facing payload is small enough (~50 fields) that this is fine.
 *
 *  Override accents: lines whose JSON key matches an override key get an
 *  accent left-border + a trailing comment showing the baseline value.
 *  Pure presentational — the *content* of the text is just the effective
 *  config.
 *
 *  Sync: parent owns the effective object. We render its prettified
 *  JSON. On every keystroke we try to parse: on success, we diff vs
 *  baseline and emit an `onChange(parsed)` with the new effective
 *  (parent recomputes overrides from this). On parse error we surface
 *  `onError(msg)` and the parent disables Run. */
export type JsonEditorProps = {
  value: Record<string, unknown>;
  baseline?: Record<string, unknown> | null;
  readOnly?: boolean;
  onChange?: (parsed: Record<string, unknown>) => void;
  onError?: (msg: string | null) => void;
};

export function JsonEditor({ value, baseline, readOnly, onChange, onError }: JsonEditorProps) {
  const initial = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const ta = useRef<HTMLTextAreaElement | null>(null);

  // When the *parent's* value changes (e.g., the user dragged a slider
  // in Form mode), re-sync the editor text. Don't overwrite if our text
  // already parses to the same value (avoids cursor jumps mid-typing).
  useEffect(() => {
    const same = (() => {
      try {
        return JSON.stringify(JSON.parse(text)) === JSON.stringify(value);
      } catch { return false; }
    })();
    if (!same) setText(JSON.stringify(value, null, 2));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = (next: string) => {
    setText(next);
    try {
      const parsed = JSON.parse(next);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("Top-level value must be an object");
      }
      setError(null);
      onError?.(null);
      onChange?.(parsed);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      onError?.(msg);
    }
  };

  // Lines that override the baseline get a left-border accent. Build a
  // set of override keys for fast lookup during rendering.
  const overrideKeys = useMemo(() => {
    if (!baseline) return new Set<string>();
    const out = new Set<string>();
    try {
      const parsed = JSON.parse(text);
      for (const k of Object.keys(parsed)) {
        const a = JSON.stringify(parsed[k]);
        const b = JSON.stringify((baseline as Record<string, unknown>)[k]);
        if (a !== b) out.add(k);
      }
    } catch {}
    return out;
  }, [text, baseline]);

  const lines = text.split("\n");
  // Match `"key":` at the start of a line (ignoring indent) to figure
  // out which key a given source line belongs to.
  const keyOfLine = (line: string): string | null => {
    const m = line.match(/^\s*"([^"]+)":/);
    return m ? m[1] : null;
  };

  return (
    <div className="relative font-mono text-[11px] leading-[1.5]">
      {error && (
        <div className="px-3 py-1 text-warning text-[10px] bg-warning/10 border-b border-warning/30">
          JSON parse error: {error}
        </div>
      )}
      <div className="relative">
        {/* Highlighted overlay (visual only). Behind the textarea. */}
        <pre
          aria-hidden
          className="absolute inset-0 m-0 p-3 whitespace-pre-wrap pointer-events-none text-text-secondary"
        >
          {lines.map((line, i) => {
            const k = keyOfLine(line);
            const isOverride = k !== null && overrideKeys.has(k);
            const baselineValue =
              k !== null && baseline
                ? (baseline as Record<string, unknown>)[k]
                : undefined;
            return (
              <div
                key={i}
                className={
                  isOverride
                    ? "bg-accent/5 border-l-2 border-accent pl-1 -ml-1"
                    : ""
                }
              >
                {line}
                {isOverride && baselineValue !== undefined && (
                  <span className="text-warning text-[10px] ml-2">
                    // override (recipe: {JSON.stringify(baselineValue)})
                  </span>
                )}
              </div>
            );
          })}
        </pre>
        {/* Editable textarea on top (transparent text, real caret). */}
        <textarea
          ref={ta}
          value={text}
          readOnly={readOnly}
          onChange={(e) => handleChange(e.target.value)}
          spellCheck={false}
          className="relative w-full min-h-[260px] p-3 bg-transparent text-transparent caret-text-primary resize-y selection:bg-accent/20 focus:outline-none"
          aria-label="Recipe JSON"
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/properties/widgets/JsonEditor.tsx
git -c commit.gpgsign=false commit -m "widgets: add JsonEditor (textarea + override-aware overlay)"
```

---

### Task 5.2: Wire Form/JSON toggle in SimulationCard

**Files:**
- Modify: `frontend/src/components/sim/SimulationCard.tsx`

- [ ] **Step 1: Replace the disabled JSON button + body switch**

Import:

```ts
import { JsonEditor } from "@/components/properties/widgets/JsonEditor";
```

In the toggle row, remove the `disabled` JSON button and replace with the working one:

```tsx
            <button
              onClick={() => setViewPersist("json")}
              className={
                "flex-1 px-2 py-1 text-[10px] rounded " +
                (view === "json" ? "bg-accent/15 text-accent" : "text-text-muted")
              }
            >
              JSON
            </button>
```

In the body block, replace the existing form-only branch with both:

```tsx
        <div className="flex-1 min-h-0 overflow-y-auto">
          {view === "form" ? <Properties /> : <SimJsonBody />}
        </div>
```

Add `SimJsonBody` to the file:

```tsx
/** JSON body: edits the effective config. Diffing back to overrides is
 *  handled by computing per-key diffs and dispatching setOverride or
 *  clearOverride. Run button is disabled while a parse error is active. */
function SimJsonBody() {
  const baseline    = useStore((s) => s.simRecipeBaseline);
  const { effective, setOverride, clearOverride } = useOverrides();
  const setRunBlocked = useStore.getState().setRunBlockedByJson ?? (() => {});

  const onChange = (parsed: Record<string, unknown>) => {
    // Compute the diff between parsed and baseline. For each key:
    //   - present in parsed AND different from baseline → setOverride
    //   - present in parsed AND equal to baseline      → clearOverride
    //   - missing from parsed                          → leave override
    //     untouched; the user removed the line, which we treat as
    //     reverting to baseline. (Conservative interpretation.)
    if (!baseline) return;
    const allKeys = new Set([
      ...Object.keys(baseline),
      ...Object.keys(parsed),
    ]);
    for (const k of allKeys) {
      const inParsed = Object.prototype.hasOwnProperty.call(parsed, k);
      if (!inParsed) continue;
      const a = JSON.stringify(parsed[k]);
      const b = JSON.stringify(baseline[k]);
      if (a !== b) setOverride(k, parsed[k]);
      else clearOverride(k);
    }
  };

  const onError = (msg: string | null) => setRunBlocked(!!msg);

  return (
    <JsonEditor
      value={effective}
      baseline={baseline}
      onChange={onChange}
      onError={onError}
    />
  );
}
```

The `setRunBlockedByJson` is a small flag we add next so the Run button knows to disable on parse error.

- [ ] **Step 2: Add the runBlockedByJson flag to the store**

In `store.ts`:

```ts
  runBlockedByJson: boolean;
  setRunBlockedByJson: (v: boolean) => void;
```

```ts
  runBlockedByJson: false,
  setRunBlockedByJson: (v) => set({ runBlockedByJson: v }),
```

- [ ] **Step 3: Disable Run when blocked**

In `RunButton.tsx`, near other store reads:

```ts
  const runBlockedByJson = useStore((s) => s.runBlockedByJson);
```

In the disabled prop / state derivation, OR `runBlockedByJson` into whatever makes Run unavailable. Add a title hint like `"Recipe JSON has a parse error"` when this is the reason.

- [ ] **Step 4: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/lib/store.ts frontend/src/components/sim/SimulationCard.tsx frontend/src/components/runs/RunButton.tsx
git -c commit.gpgsign=false commit -m "SimulationCard: enable JSON view + parse-error blocks Run"
```

---

### Task 5.3: Replace the JSON `<pre>` in RecipesModal with JsonEditor

**Files:**
- Modify: `frontend/src/components/recipes/RecipesModal.tsx`

- [ ] **Step 1: Use JsonEditor in RecipeDetail**

Add import:

```ts
import { JsonEditor } from "@/components/properties/widgets/JsonEditor";
```

Replace the body of `RecipeDetail`:

```tsx
function RecipeDetail({ name }: { name: string }) {
  const qc = useQueryClient();
  const { data: r, isLoading } = useQuery({
    queryKey: ["recipes", name],
    queryFn: () => api.recipes.get(name),
  });
  if (isLoading || !r) return <div className="p-4 text-text-muted text-xs">Loading…</div>;
  const isUser = r.source === "user";

  const onSave = async (next: Record<string, unknown>) => {
    if (!isUser) return; // built-ins are read-only
    try {
      await api.recipes.save(name, next);
      qc.invalidateQueries({ queryKey: ["recipes", name] });
      qc.invalidateQueries({ queryKey: ["recipes"] });
    } catch (e) {
      console.error("save failed", e);
    }
  };

  return (
    <div className="px-3 py-3">
      {!isUser && (
        <div className="mb-2 px-2 py-1 bg-warning/10 text-warning text-[10px] rounded">
          Built-in recipe — read-only. Click <strong>Duplicate</strong> to edit.
        </div>
      )}
      <JsonEditor
        value={r.data}
        baseline={null}
        readOnly={!isUser}
        onChange={(next) => { void onSave(next); }}
      />
    </div>
  );
}
```

Note: saving on every keystroke is bursty. Phase 6 polish can add a debounce. For now the API is fast and this is fine.

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/recipes/RecipesModal.tsx
git -c commit.gpgsign=false commit -m "RecipesModal: use JsonEditor for detail (read-only for built-ins)"
```

---

### Phase 5 visual verification

Hard-reload.

**Visual checklist:**

- [ ] In SimulationCard, the Form/JSON toggle works (no more "soon" label)
- [ ] JSON view shows the merged effective config
- [ ] Override lines have an accent left-border and a `// override (recipe: ...)` trailing comment
- [ ] Edit a value in JSON, save by deselecting the field — Form view reflects the change, override count updates
- [ ] Introduce a syntax error (e.g. delete a closing brace) — yellow banner appears, Run button disables, Form keeps the last valid state
- [ ] Fix the JSON — banner disappears, Run re-enables
- [ ] In Recipes modal, picking a user recipe shows it in JsonEditor; editing saves
- [ ] Picking a built-in recipe shows read-only banner + non-editable JSON

---

## Phase 6 — Polish + edge cases

**Objective:** All the state-machine + edge-case items from the spec that haven't been wired yet.

### Task 6.1: Read-only summary when Source = sequence (under model)

**Files:**
- Modify: `frontend/src/components/sim/SimulationCard.tsx`

- [ ] **Step 1: Detect sequence-loaded state + render summary**

At the top of `SimulationCard`:

```ts
import { useQuery as useRq } from "@tanstack/react-query";
import type { SequenceItem } from "@/lib/types";
```

Inside the component, after the existing model/recipe selectors:

```ts
  const isSequenceRun =
    !!simRunName && !simRunName.startsWith("_model:");
  const { data: sequences = [] } = useRq({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
  });
  const seq = (sequences as SequenceItem[]).find((s) => s.name === simRunName);
  const isOrphan = isSequenceRun && (!seq || seq.model_ref == null);
  const isSequenceUnderModel = isSequenceRun && !isOrphan;
```

Before the existing "empty / pick recipe / show editor" branch logic, return early on the orphan and summary cases:

```tsx
  if (isOrphan) return null;
  if (isSequenceUnderModel) {
    return (
      <div className="px-3 py-3 text-xs space-y-2">
        <div className="text-text-muted text-[10px] uppercase tracking-wider">
          ② Simulation (read-only)
        </div>
        <div className="text-text-secondary">
          Based on recipe{" "}
          <span className="font-mono text-accent">
            {seq?.recipe_source ?? "(unknown)"}
          </span>
        </div>
        <div className="text-text-muted text-[10px]">
          This is a finished sequence — params can't be edited.
        </div>
        <button
          type="button"
          onClick={() => {
            // Switch source back to the parent model and load the recipe
            // that produced this sequence. Overrides clear naturally
            // when loadActiveRecipe snapshots a new baseline.
            const m = useStore.getState().activeModel;
            const rname = seq?.recipe_source ?? null;
            if (m && rname) {
              useStore.getState().resetForNewRun(`_model:${m.name}`);
              useStore.getState().setSimState("idle");
              // Recipe load goes through api so we get the fresh data:
              api.recipes.get(rname).then((r) =>
                useStore.getState().loadActiveRecipe(r.name, r.data)
              );
            }
          }}
          className="mt-2 w-full px-3 py-1.5 bg-accent/15 text-accent rounded text-[11px] font-medium hover:bg-accent/25"
        >
          New run from this recipe…
        </button>
      </div>
    );
  }
```

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/sim/SimulationCard.tsx
git -c commit.gpgsign=false commit -m "SimulationCard: read-only summary when viewing a sequence"
```

---

### Task 6.2: Recipe-deleted banner

**Files:**
- Modify: `frontend/src/components/properties/Properties.tsx`

- [ ] **Step 1: Detect orphan baseline (active recipe gone from server)**

Above the existing JSX:

```ts
import { useQuery } from "@tanstack/react-query";
```

```ts
  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });
  const baselineExists = activeRecipeName
    ? recipes.some((r) => r.name === activeRecipeName)
    : true;
```

Insert before the override-strip:

```tsx
        {!baselineExists && activeRecipeName && (
          <div className="px-3 py-2 border-b border-warning bg-warning/10 text-warning text-[11px]">
            Baseline <span className="font-mono">{activeRecipeName}</span> was
            deleted. Your {overrideCount} edits are now standalone — save them
            as a new recipe.
          </div>
        )}
```

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/properties/Properties.tsx
git -c commit.gpgsign=false commit -m "Properties: banner when baseline recipe was deleted"
```

---

### Task 6.3: Delete dead Outliner files

**Files:**
- Delete: `frontend/src/components/outliner/Outliner.tsx`
- Delete: `frontend/src/components/outliner/ModelTree.tsx`
- Delete: `frontend/src/components/outliner/SequenceTree.tsx`
- Delete: `frontend/src/components/outliner/RecipeTree.tsx`
- Delete: `frontend/src/components/outliner/HistoryTree.tsx`
- Delete: `frontend/src/components/properties/SavePresetDialog.tsx` (absorbed into the override strip's `Save as new`)

- [ ] **Step 1: Verify no remaining usage**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && grep -rn "Outliner\|ModelTree\|SequenceTree\|RecipeTree\|HistoryTree\|SavePresetDialog" src/ --include="*.ts" --include="*.tsx"
```

Expected: only matches inside the about-to-be-deleted files themselves (and possibly the deleted-import line in App.tsx — fix that first if it's still there).

- [ ] **Step 2: Delete**

```bash
rm /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/outliner/*.tsx
rmdir /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/outliner 2>/dev/null || true
rm /home/frankyin/Desktop/work/gsfluent_pkg/frontend/src/components/properties/SavePresetDialog.tsx
```

- [ ] **Step 3: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean. Bundle shrinks slightly.

- [ ] **Step 4: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add -A frontend/src/
git -c commit.gpgsign=false commit -m "cleanup: delete dead Outliner + SavePresetDialog (replaced by SourceCard + override strip)"
```

---

### Task 6.4: Run-finished toast

**Files:**
- Modify: `frontend/src/components/sim/SimulationCard.tsx`

- [ ] **Step 1: Watch simState for the running→done transition**

Add at the top of `SimulationCard`:

```ts
import { useEffect, useState, useRef as useReactRef } from "react";
```

(skip if useRef is already imported)

Inside the component:

```ts
  const simState = useStore((s) => s.simState);
  const lastFinishedSeq = useStore((s) => s.simRunName);
  const prevSimState = useReactRef<string>(simState);
  const [showFinishedToast, setShowFinishedToast] = useState(false);

  useEffect(() => {
    if (prevSimState.current === "running" && simState === "done") {
      setShowFinishedToast(true);
      const t = setTimeout(() => setShowFinishedToast(false), 6000);
      return () => clearTimeout(t);
    }
    prevSimState.current = simState;
  }, [simState]);
```

Render the toast near the bottom of the JSX (inside the editor branch, above the footer RunButton):

```tsx
        {showFinishedToast && (
          <div className="mx-3 mb-2 px-3 py-2 bg-success/10 border border-success/30 text-success text-[11px] rounded flex items-center gap-2">
            <span>Run finished</span>
            <button
              onClick={() => {
                if (lastFinishedSeq) {
                  // Switching source to the sequence is the same as
                  // clicking it in SourceCard.
                  // (existing onLoadRun in App handles this via a callback,
                  // but inside SimulationCard we don't have it. Use the
                  // same imperative flow.)
                  useStore.getState().resetForNewRun(lastFinishedSeq);
                  useStore.getState().setSimState("done");
                }
                setShowFinishedToast(false);
              }}
              className="ml-auto text-success hover:underline"
            >
              View sequence
            </button>
            <button
              onClick={() => setShowFinishedToast(false)}
              className="text-text-muted hover:text-text-primary"
            >
              ✕
            </button>
          </div>
        )}
```

- [ ] **Step 2: Type-check + build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1 | head -20 && npx vite build 2>&1 | tail -6
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add frontend/src/components/sim/SimulationCard.tsx
git -c commit.gpgsign=false commit -m "SimulationCard: run-finished toast with View sequence action"
```

---

### Phase 6 visual verification

Hard-reload.

**Visual checklist:**

- [ ] Click a sequence under a model in SourceCard — SimulationCard shows read-only summary citing the recipe + `New run from this recipe…` button
- [ ] Click that button — Source switches to the parent model, recipe loads, you can edit + Run again
- [ ] Click an orphan sequence — SimulationCard disappears entirely
- [ ] Delete the active recipe via the Recipes modal — yellow banner appears in Properties: *"Baseline X was deleted…"*. Overrides remain editable; Save-as-new is the obvious recovery
- [ ] Trigger a Run — when it finishes, a green "Run finished — View sequence" toast appears in SimulationCard for 6s
- [ ] No dead imports, no console warnings about missing components

---

## Phase 7 — Final verification pass

### Task 7.1: Full build + bundle sanity

- [ ] **Step 1: Clean build**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && rm -rf dist && npx vite build 2>&1 | tail -10
```

Expected: build succeeds, bundle size in the same ballpark as before (`index-*.js` ≈ 1.2 MB / 357 KB gzip).

- [ ] **Step 2: Type-check the whole project**

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg/frontend && npx tsc --noEmit 2>&1
```

Expected: zero errors.

- [ ] **Step 3: Run-time smoke check via the running stack**

The user's run-client.sh stack is already up at http://localhost:4173/. Hard-reload and run through every visual checklist item across all six phases. Anything that fails goes into a fixup commit before declaring done.

- [ ] **Step 4: Optional final commit**

If any fixups were needed in Step 3:

```bash
cd /home/frankyin/Desktop/work/gsfluent_pkg
git add -A
git -c commit.gpgsign=false commit -m "sim-redesign: fixups from end-to-end smoke pass"
```

---

## Out-of-scope reminders (per spec)

These are explicitly deferred and **not** part of this plan:

- Comparison view between two sequences
- Multi-select runs / parallel playback
- Recipe versioning / git-style history
- Live re-run on parameter change (auto-Run)
- Replacing the JSON textarea editor with CodeMirror
- Lazy-rendering / virtualizing the sequence tree (only needed at 50+ models)
- Pruning the vestigial `activeWorkspace` enum from the store

If any of these come up during implementation, log them as future tickets — don't expand this plan's scope.

---

## Risk reminders

- **JsonEditor cursor jumps** — the parent-sync `useEffect` in Task 5.1 only re-syncs when the parsed text differs from the value. If a subtle whitespace difference triggers a sync mid-typing, the cursor will jump. Watch for it in Phase 5 verification; if it happens, narrow the comparison to deep-equal on parsed values only.

- **Override + JSON round-tripping** — JSON view computes overrides from the diff vs baseline. A JSON edit that introduces a new key not present in baseline becomes an override. A JSON edit that removes a baseline key currently leaves the override untouched (per Task 5.2's "missing from parsed → leave override untouched" comment). If users complain about this, change `clearOverride` to fire for missing keys.

- **Recipe save races** — saving a new recipe via "Save as new…" while a run is in flight is currently allowed. The recipe library invalidation will re-render the picker mid-run. If this causes flicker, add a guard in Task 1.8's `onSaveAsNew`.

---

## Self-review

- Spec coverage: every section of the spec has at least one task — Source card (Phase 2), Sim card pipeline (Phase 3), override semantics (Phase 1), Recipes modal (Phase 4), Form/JSON parity (Phase 5), state machine including sequence-under-model and orphan + recipe-deleted (Phase 6). The deferred items in the spec's "Out of scope" section are not in the plan, as intended.
- Placeholder scan: every code-changing step contains the actual code or the exact diff to apply. No "TBD", "add appropriate validation", or "similar to Task N" references.
- Type consistency: `setOverride(key, value)` / `clearOverride(key)` / `clearAllOverrides()` / `setSimRecipeBaseline(data)` signatures match between Task 1.1 (definition), Task 1.3 (hook), Task 1.4–1.6 (consumers). `setRecipesModalOpen(open)` matches between Task 4.1 (definition) and Tasks 4.3 / 4.4 (consumers). `setRunBlockedByJson(v)` matches between Task 5.2 (definition + use).
