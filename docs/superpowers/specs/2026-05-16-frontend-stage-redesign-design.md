# Design — Frontend "Stage" redesign (foundations slice)

**Date:** 2026-05-16
**Status:** approved, ready for implementation plan
**Scope:** Visual identity (tokens, motion, typography) + AppShell layout + intuitive UX flow + format-aware DropZone. Excludes the viewport's internal 3D rendering, the sim runner, the docker deployment (shipped separately), and Splats / Points renderer internals.

## Why

User-reported pain: "the accessibility, everything is confusing, performance, bla bla bla, we need to redesign." The current frontend is a functional 3-pane shell with no visual identity, dense ad-hoc Tailwind tokens, and a UX flow that doesn't guide first-time users through "load a model → pick a recipe → run a sim". Teammate feedback corroborates: drag-drop and model switching are confusing.

The frontend is also the leader's first impression next month. The "deploy as Docker, leader uses it on their laptop" goal needs a UI that looks like a real product.

## Direction (approved)

- **Visual**: cinematic / shader-y (reference: Frame.io, Three.js Editor, Spline). Bold contrast, glassy panels, subtle gradients, motion on focus. Pairs naturally with the 3DGS imagery in the viewport.
- **Layout**: viewport-first. Canvas dominates; panels float as glass cards.

## Architecture overview

```
<AppShell>
  <Viewport />                          {/* absolute inset-0, z-0 — fullscreen R3F canvas */}
  <TopBar />                            {/* fixed top, z-30, h-12 */}
  <FloatingOutliner />                  {/* fixed left, z-20, glass card */}
  <FloatingProperties />                {/* fixed right, z-20, glass card */}
  <PlaybackDock />                      {/* fixed bottom-center, z-20, glass card */}
  <CommandPalette />                    {/* modal, z-50 */}
</AppShell>
```

z-index ordering: viewport 0 → cards 20 → topbar 30 → modal 50. No nested stacking contexts inside cards.

## Design tokens

### Color (semantic — referenced everywhere via Tailwind utility classes)

```
canvas         #0a0f1a    deeper than current; backdrop for floating cards
elevated       #141a26    card surface (88% alpha when glassed)
border         #1f2937    inactive; #2a3441 active
text-primary   #e5edf5
text-secondary #94a3b8
text-muted     #94a3b8    (was #64748b — bumped for WCAG AA)
accent         #22d3ee    keep current cyan; works against the dark canvas
accent-glow    rgba(34, 211, 238, 0.4)   focus + active outlines
warn           #fbbf24
error          #f87171
success        #34d399
```

Glass card surface: `bg-elevated/85` + `backdrop-blur-md` + `border-border/50` + inset shadow.

### Type

- UI: `Inter Variable` via `@fontsource-variable/inter`. Variable axis so we can use 400-700 weights without separate file loads.
- Mono: `JetBrains Mono Variable` via `@fontsource-variable/jetbrains-mono`. Tabular numerals for the Properties panel.
- Scale: `10 / 12 / 13 / 15 / 18 / 24` px. Drop the existing `text-[10px]/text-[11px]/text-xs/text-sm` zoo.

### Spacing

4 px base unit. Cards `p-3` (12 px). Section gaps `gap-2` (8 px) inside cards, `gap-3` (12 px) between cards. Outer card gutter from viewport edges: 12 px.

### Motion

Three constants in `tailwind.config.js`:

```js
transitionDuration: {
  fast: '150ms',     // hover / focus
  panel: '200ms',    // panel show / hide / collapse
  swap: '300ms',     // workspace switch
}
transitionTimingFunction: {
  // Material's "standard easing"
  motion: 'cubic-bezier(0.2, 0, 0, 1)',
}
```

Every animation gated on `@media (prefers-reduced-motion: no-preference)`. Without it, animations collapse to opacity-only fades over 50 ms.

### Elevation

```css
.glass-card {
  @apply bg-elevated/85 backdrop-blur-md
         border border-border/50
         shadow-[0_8px_32px_-8px_rgba(0,0,0,0.6),0_0_0_1px_rgba(255,255,255,0.04)]
         rounded-xl;   /* 12 px */
}
```

One shadow token, one border-radius standard for cards (`rounded-xl`), one for buttons inside cards (`rounded-md` = 6 px).

## Components

### `<TopBar />`  (h-12, 48 px)

Thin bar pinned to top with `bg-canvas/60 backdrop-blur-md`. Layout:

```
[Brand+dot] · [ws-chips: Sim | Recipes] · [breadcrumb: model / recipe / seq] ← flex-1 → [StatusPill] [RunButton]
```

- **Brand**: 10 px gradient dot (`bg-gradient-to-br from-accent to-violet-500`) + "gsfluent" wordmark.
- **ws-chips**: pill group with active highlight. Replaces the current `WorkspaceTabs`.
- **breadcrumb**: shows `<active-model>` / `<active-recipe>` / `<active-sequence>` if set. Each segment clickable → focuses the matching panel.
- **StatusPill**: existing 3-dot Backend/Sync/Viser pill, retained.
- **RunButton**: stateful (see §"Run button" in UX flow).

### `<FloatingOutliner />`

`fixed left-3 top-16 bottom-3 w-72` glass card. Sections:

1. **Models** — list of registered models, drag-drop + path-paste inputs at top
2. **Sequences** — list of sequence cards with provenance badges
3. **Recipes** — list of built-in + user-saved recipes
4. **History** — past sim runs

Each section has a collapsible header with chevron + count. Active row gets `border-l-2 border-accent` + faint accent background. Resize handle on right edge (drag). Collapse button → 40 px icon rail.

State (Zustand, persisted to localStorage): `panels.outliner: 'expanded' | 'collapsed'`, `outlinerWidth: number`.
Keyboard: `Ctrl/Cmd-B` toggles collapsed.

### `<FloatingProperties />`

`fixed right-3 top-16 w-80 max-h-[calc(100vh-80px)]` glass card. Slides in from right when a recipe is active.

Visibility logic: `properties.show = activeRecipeData != null`. Slides in from right when becoming visible, out when leaving. Collapse → bottom-corner mini-pill `Ctrl/Cmd-I` to expand.

Inside: the existing Properties tree (Material / Solver / Forces / etc.), unchanged content, restyled visuals.

### `<PlaybackDock />`

`fixed bottom-3 left-1/2 -translate-x-1/2 w-[min(80vw,800px)]` glass card. Only visible when `simRunName` is set. Layout:

```
[▶ play 40px] [scrubber + frame info] [speed chips: 0.5× 1× 2×] [loop toggle]
```

Auto-hides 4 s after camera idle (saves screen real estate during cinematic playback). Comes back on any input.

Scrubber: 8 px tall (4 px when idle), thumb 12 px, fill gradient `from-accent to-cyan-600`. Frame ticks every 30 frames as 1 px dividers. Frame counter + fps to the right, monospace.

### `<GlassCard />` primitive

Single reusable component:

```tsx
<GlassCard
  side="left" | "right" | "bottom"   // drives slide-in direction
  collapsed={boolean}
  onCollapse={() => void}
  shortcut="⌘B"                       // shown in collapse-chevron's title
>
  <GlassCard.Header>...</GlassCard.Header>
  <GlassCard.Body>...</GlassCard.Body>
</GlassCard>
```

Used by Outliner, Properties, Playback. Header has: drag-grip dots (visual only v1), title, collapse chevron, optional pin button. Body `overflow-y-auto`.

### `<DropZone />` (format-aware)

Replaces the current model-only DropZone:

- Dragged `.ply` → "Drop to add as **model**" overlay + Y-up toggle. POST `/api/models/upload`.
- Dragged `.npz` → "Drop to add as **sequence**" overlay (no Y-up — coord convention is baked into the npz). POST `/api/sequences/upload-npz` (new endpoint).
- Dragged anything else → toast: "Drop a .ply (model) or .npz (sequence)".

Implementation: read `file.name` extension in the `dragover` event, swap the overlay copy accordingly. The drop itself dispatches to the right API based on extension.

Backend (`server/gsfluent/api/sequences.py`) — new endpoint:

```
POST /api/sequences/upload-npz
  multipart/form-data:
    file: .npz binary
    name: str (optional, defaults to basename)

  →  1. magic byte check (PK\x03\x04 zip header)
     2. save streamed to work/cache/viser/<name>.npz
     3. open with np.load(mmap_mode="r"), read shapes to derive
        frame_count + n_splats (works for both v1 and v2 schemas)
     4. write work/library/sequences/<name>/_meta.json with
        source="import", coord_convention="z-up", derived counts
     5. return SequenceItem
```

Size limit: 4 GB (generous, our .npz files run 2-3 GB). Errors: 422 if not a valid npz, 422 if expected keys missing.

### Empty states (per panel)

- **Outliner / Models**: large dashed-border drop area "Drag a .ply onto the viewport" + arrow pointing at canvas. Arrow gently bobs every 2 s (reduced-motion: static).
- **Outliner / Sequences**: "Run a sim or drop a .npz to add one" with the matching icon.
- **Outliner / Recipes**: never empty (built-ins).
- **Properties**: muted "Pick a recipe in the Outliner" + small icon.
- **Viewport**: numbered guide (1) Load a model · (2) Pick a recipe · (3) ▶ Run. Each step ticks off as state advances. Auto-hides after first successful run, stays gone for the session.

## Intuitive UX flow (8 commitments)

These don't add features — they make the existing flow self-explanatory.

### 1. Viewport onboarding guide
3-step card-sized hint anchored to the action it triggers. Ticks off as state advances. Auto-hides after first run.

### 2. Run button has 5 states

| state | visual |
|---|---|
| `idle, no model+recipe` | grey + "Load model + recipe to run" |
| `idle, ready` | accent gradient + faint pulse + "Run" + cost preview `~3 min · 200k particles` |
| `running` | orange + spinner + percent + ETA + "Cancel" |
| `done, just finished` | green flash for 2 s, then "Run again" |
| `error` | red + inline error message + click expands logs |

Run progress consolidates into the Run button. The current scattered StatusPill+StatusStrip+Console reporting becomes a single source of truth.

### 3. Recipe dirty state, never silent
Editing in Properties marks recipe `★ jelly_native (modified)`. Header gets Save/Discard pill. Switching recipes with unsaved edits → toast: "Save / Discard / Cancel".

### 4. Sequence cards show provenance
```
api_jelly_native_200k_1778820309
cluster_6_15 · jelly_native · 2 h ago · 151f · 683k
```
Model + recipe names are clickable, jump focus to their panel.

### 5. State-driven affordances
Properties panel hidden when no recipe is picked. Playback dock hidden when no sequence active. Workspace tabs grey-out when not applicable. No "click but nothing happens".

### 6. Breadcrumb in topbar
`cluster_6_15 / jelly_native / smoke_1778830754`. Each segment clickable. User always knows their position in the model/recipe/sequence tree.

### 7. Mode-aware sequence rows
Hover any sequence → tooltip says "Plays in Splats mode (high quality)" or "Plays in Points mode (Splats cache not built)". User never wonders why something looks different.

### 8. Drop preview / confirmation
Dropping a file shows what will happen ("Will upload as model") before commit, with Cancel. Currently the drop is instant — surprising.

## Accessibility plan

- **Semantic HTML**: `<aside role="complementary" aria-label="Outliner">` for floating panels, `<header role="banner">` for topbar, `<main role="main">` wraps viewport, `<dialog>` for command palette (native focus trapping).
- **Tab order**: brand → workspace switcher → run → status → outliner → viewport → properties → playback.
- **Skip-link**: `Cmd/Ctrl-/` jumps focus directly into the active panel.
- **Command palette**: `Cmd/Ctrl-K` (already wired inside CommandPalette.tsx — keep as-is).
- **Card collapse**: `Cmd/Ctrl-B` (outliner), `Cmd/Ctrl-I` (properties).
- **Playback**: Space/Arrow keys when playback dock has focus (re-introduces a scoped keyboard layer; was removed earlier as too-global).
- **Focus management**: slide-in cards auto-focus their first interactive child after animation completes (or instantly if reduced-motion). Closing returns focus to the trigger.
- **Contrast**: every text-bg pair tested against WCAG AA (≥ 4.5:1 normal, 3:1 large). Bumped `text-muted` from `#64748b` to `#94a3b8`.
- **ARIA live region**: single `<div role="status" aria-live="polite" aria-atomic="true">` for sim state announcements. Replaces silent state changes.
- **Reduced motion**: every animation behind `@media (prefers-reduced-motion: no-preference)`; falls back to opacity-only over 50 ms.

## Performance plan

- **React-query dedup**: verify on the network panel that the two `["sequences"]` queries in App.tsx and SequenceTree are actually de-duped (react-query usually does this by key). If not, pull into one provider-level query. Same audit for `["models"]`, `["recipes"]`.
- **Virtualize sequence list**: when count > 30, swap `.map()` for `@tanstack/react-virtual` (already a transitive dep). Future-proofs the library growing over time.
- **Code-split workspaces**: `RecipesWorkspace` is only loaded when its chip is clicked. `React.lazy()` + `<Suspense>` with a card skeleton. Saves ~25 kB gzipped from initial bundle.
- **Memoize viewport children**: audit `<Viewport>` and wrap sub-components in `React.memo` where they don't already use it (`PlaybackBar`, `SplatScene`, `ViserSplatScene`).
- **Bundle**: drop `@radix-ui/react-tooltip` (~12 kB gz) — replace its three call-sites with a 30-line custom tooltip on plain `<details>` or CSS-only. We don't need its full API.
- **Backdrop blur**: GPU-cheap on all three major engines (chromium / firefox / webkit) per quick local test. Fallback to opaque background via `@supports not (backdrop-filter)` — covers the no-GPU-compositor case.

## Migration path

The new shell sits alongside the current one behind a feature flag. Ship incrementally so the team isn't blocked on a big-bang merge.

**Phase 1 — Tokens + GlassCard primitive** (no visual change to users yet)
- Add Inter + JetBrains Mono via fontsource
- Extend `tailwind.config.js` with the new color tokens, type scale, motion constants
- Add `globals.css` rule for `.glass-card`
- Add the `<GlassCard>` primitive component (with collapsed/show transitions)
- Verify nothing visually changes — existing components still use old utility classes
- Ship

**Phase 2 — TopBar redesign** (visible)
- Replace `<TopBar>` + `<WorkspaceTabs>` with a unified topbar that has the brand + ws-chips + breadcrumb + StatusPill + RunButton
- New RunButton state machine (5 states)
- StatusStrip stays put for now — it's still useful for sim progress
- Ship

**Phase 3 — Stage layout** (visible, breaking visual change)
- New `<AppShell>` that mounts Viewport fullscreen + FloatingOutliner + FloatingProperties + PlaybackDock as fixed-position children
- Remove the old `react-resizable-panels` PanelGroup
- Wire up `panels` slice in Zustand for collapse state
- Wire up keyboard shortcuts (scoped to playback dock + global Cmd-B/Cmd-I)
- Ship

**Phase 4 — Outliner + Properties polish + UX flow**
- Outliner sections refactored into the new card layout
- Properties slide-in logic
- Sequence cards show provenance
- Empty states (viewport guide, panel hints)
- Recipe dirty state + toast on switch
- Mode-aware sequence row tooltips
- Ship

**Phase 5 — DropZone format-aware + npz upload**
- DropZone reads file extension in dragover
- New server endpoint `POST /api/sequences/upload-npz`
- Drop preview overlay
- Ship

**Phase 6 — Accessibility + performance polish**
- ARIA labels + live region
- Skip-link + scoped keyboard layer
- React-query audit + dedup
- Bundle size: drop radix-tooltip, code-split RecipesWorkspace
- Reduced-motion fallbacks
- Ship

Each phase is independently mergeable + testable. Phase 1 lays the foundation invisibly; Phases 2-6 ship visible improvements with clear scope.

## Out of scope (this slice)

- Viewport / 3D rendering internals — keep current Splats + Points renderers
- Sim core / runner.py — unchanged
- Docker / deployment — handled in a separate slice (already shipped)
- CommandPalette internals — keep current, just restyle to glass

## Open questions

- Should the playback dock auto-hide on idle? (Spec says yes, but might be too aggressive — leave as a tunable flag and decide after using it)
- Variable font tradeoff: ~70 kB for Inter Variable. Acceptable for a workbench, but could degrade to system stack on slow connections via `font-display: optional`. Decide during Phase 1.

## Success criteria

1. Empty workbench shows a clear "what to do next" path; first-time user reaches a successful sim run without docs.
2. Lighthouse accessibility score ≥ 95 on the main workspace.
3. Initial JS bundle ≤ 350 kB gzipped (currently ~420 kB).
4. Tab key reaches every interactive element; visible focus indicators throughout.
5. Reduced-motion testers see no animations beyond 50 ms opacity fades.
6. Recipe edits are never silently lost (dirty-state toast or persisted).
