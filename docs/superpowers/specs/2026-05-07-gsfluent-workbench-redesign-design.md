# gsfluent workbench — production redesign

**Status:** Draft (2026-05-07)
**Owner:** FrankYin
**Replaces:** The viser-based `tools/workbench.py` (Python panel inside a viser scene)

## Summary

Replace the existing viser-based workbench with a production-grade, browser-served React app inspired by Blender's three-zone layout and Cursor/Linear's elevated-dark aesthetic. Keeps the existing Python sim core (Taichi + Warp + PyTorch CUDA extensions) untouched; rebuilds only the user-facing frontend + a thin FastAPI bridge.

The current viser implementation hit its ceiling: the sidebar isn't drag-resizable, panel primitives are fixed, parameter folders pile up vertically, and recipe authoring is painful enough that authors must hand-edit JSON for `boundary_conditions`. This redesign solves all of that with a real React stack while preserving the sim pipeline that already works.

## Goals

1. **Production-grade visual identity.** Looks at home next to Linear, Cursor, Figma, modern Houdini. No "research tool with effort." Dark theme, cyan accent, monospace numerics, glass header.
2. **Dual-audience UX.** A power user (the project owner) gets density, keyboard shortcuts, and full parameter control. A casual user (teammate, partner) can drag-drop a `.ply`, pick a preset, hit Run.
3. **Recipe authoring as a first-class workflow,** not raw-JSON editing. Visual list editor for boundary conditions, material auto-fill, provenance, recipe-coresaved-with-runs.
4. **Reproducibility by construction.** Every run records the exact effective recipe + the model. Opening any past run reconstitutes the inputs.
5. **Single-installer expectation.** `pip install gsfluent && gsfluent serve` opens a browser window. No separate frontend deployment, no node toolchain on user machines.

## Non-goals

- Re-implementing the sim core. Taichi/Warp/PyTorch sim_one.sh stays as-is, treated as a subprocess.
- Drag-resizable / drag-to-split panel system at full Blender fidelity. We get most of it via `react-resizable-panels`; full-fidelity area-merge is Phase 3.
- Multi-user collaboration / shared sessions.
- Mobile/tablet support.
- Compare or Render workspaces. Both ship as placeholder tabs in MVP and become Phase 2 work.
- Live CFL/stability validation. Solver-side errors will surface fast enough that we rely on the solver's own diagnostics rather than re-implementing them in TypeScript. Revisited in Phase 1.5.

## Personas + jobs-to-be-done

**Owner / power user (1 person today, the project lead).**
Daily flow: drop a `.ply`, pick a preset, tweak 2–3 params, run, watch, iterate. Knows the physics, expects efficiency. Most-used keyboard shortcut: `Cmd+Enter` to run, `i` to toggle inspector, `Cmd+K` for command palette. Cares about: speed of iteration, visual quality of viewport, knowing exactly what changed between two runs.

**Casual teammate (3–6 people, occasional users).**
Doesn't know MPM. Hands them the URL, says "drag a building in, pick `jelly`, hit Run." Cares about: nothing breaking, building looks like a building, no "what's `flip_pic_ratio`?" moments. Inspector stays collapsed for them.

**Partner / demo audience (passive — owner drives).**
Watches over a shoulder. Cares only about the viewport. The owner collapses the sidebar so the building animation fills the screen.

## Layout (three zones, Blender-faithful)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  ●  gsfluent  •  cluster_6_15  •  ★ stiff_low_g                Run    Status │  Top bar (40px, glass)
├──────────────────────────────────────────────────────────────────────────────┤
│  Sim   Compare(soon)   Render(soon)   Recipes(soon)              +           │  Workspace tabs (32px)
├────────────────────┬────────────────────────────────────┬────────────────────┤
│ Outliner           │                                    │ Properties         │
│ ─────────────────  │                                    │ ─────────────────  │
│ ▾ Models           │                                    │ ▾ Material         │
│   • cluster_6_15   │                                    │   material  jelly  │
│   • building_a     │      3D Viewport                   │   E         5,000  │
│ ▾ Recipes          │      (R3F + Gaussian Splats)       │   ν         0.380  │
│   jelly            │                                    │   density   1.000  │
│   ★ stiff_low_g    │      Drag a .ply here              │ ▾ Solver           │
│   demolition       │                                    │   n_grid    150³   │
│ ▾ History          │                                    │   Δt        100µs  │
│   ...              │                                    │ ▾ Boundary cond.   │
│                    │                                    │   ▣ bounding_box   │
│                    │                                    │   ▣ surface_coll.  │
│                    │                                    │   + Add boundary   │
├────────────────────┴────────────────────────────────────┴────────────────────┤
│  ▶  ▮▮  [████████░░░░░░░░░] 42/150     ETA 0:38         ⌘K · console ▾       │  Status strip (32px)
└──────────────────────────────────────────────────────────────────────────────┘
```

- All splits use `react-resizable-panels`. The user can drag any vertical or horizontal divider. Panel ratios persist to `localStorage`.
- Each side panel has a small header-bar widget (top-right of the pane) with a dropdown to swap its editor type ("Outliner" / "Properties" / "Console" / "History"). This is the lightweight version of Blender's fungible panels — same panel surface, swappable contents.
- The bottom strip is a fixed 32px status bar, NOT a drag-resizable pane. Houses transport controls, sim progress, ETA, and a `console ▾` accordion that expands to show the last 200 stdout lines.

## Visual identity

| Token | Value | Usage |
|---|---|---|
| `bg-canvas` | `#0d1117` | App background |
| `bg-pane` | `#0d1117` | Side panels (matches canvas; borders provide separation) |
| `bg-elevated` | `#161b22` | Hover states, inputs, dropdown items |
| `border` | `#21262d` | All panel boundaries, hairline 1px |
| `text-primary` | `#c9d1d9` | Default body text |
| `text-secondary` | `#8b949e` | Labels, placeholders |
| `text-muted` | `#6e7681` | Disabled, ghost text |
| `accent` | `#22d3ee` (cyan-400) | Active, primary actions, focus rings |
| `accent-glow` | `0 0 12px rgba(34,211,238,0.3)` | Run button + progress fill |
| `success` | `#34d399` | Status badges (running OK) |
| `warning` | `#fbbf24` | Warnings (NaN risk, OOM risk) |
| `error` | `#f87171` | Sim errors |

- **Type:** Inter (UI, 13px base) + JetBrains Mono (numerics, console, file paths). Tabular figures for numbers.
- **Spacing scale:** 4 / 8 / 12 / 16 / 24 / 32 / 48px (consistent with Tailwind defaults).
- **Iconography:** lucide-react. No emoji.
- **Header:** thin glass effect (`backdrop-blur` + 85% opacity bg).
- **Active workspace tab:** cyan underline, no fill — keeps the bar light.

## Information architecture (MVP / Sim workspace)

### Outliner (left pane)

A tree of three top-level groups:

- **Models** — every model the user has loaded this session, plus the persisted `model_history.json` from previous sessions. Click selects (sets the active model used by Run). Drag from filesystem onto the viewport adds here.
- **Recipes** — built-in recipes from `tools/recipes/` and user-saved presets from `work/_user_recipes/` (prefixed `★`). Click selects (sets the active recipe). Right-click → duplicate / rename / delete (user presets only).
- **History** — every past Run under `work/fused/`, sorted newest first. Click selects (loads frames into viewport for playback). Each entry shows: name, frame count, recipe, timestamp.

Hover any item → tooltip with full path + metadata. Search bar at the top filters across all three groups.

### Viewport (center)

- React Three Fiber scene. Camera orbits around the building.
- Default render: `@mkkellogg/gaussian-splats-3d` for the active 3DGS data + animated splat positions per frame. Falls back to point cloud if SH/scale/rot are missing.
- Drag-drop zone: anywhere in the viewport accepts `.ply` drops. On drop, file is uploaded to backend, written to `work/uploads/<token>/`, added to Outliner → Models, set as active model, viewport snaps to it.
- Overlay info (top-left, transparent): current model name, current recipe, frame index, fps. Toggleable via `Cmd+`/.
- Empty state: large drop hint ("Drag a 3DGS .ply to begin").

### Properties (right pane) — recipe editor

The MVP's centerpiece. Lives inside the Properties pane and shows the **effective recipe = active preset + edits**.

Sections (each is a collapsible folder with sticky header):

- **Material** — material dropdown (7 canonical names), then E / ν / density / yield_stress / friction_angle / β / ξ / hardening / α₀ / plastic_viscosity. Sliders + number inputs. **When `material` changes, all material params snap to that material's validated defaults** (with a one-toast-undo: "Snapped to metal defaults · Undo").
- **Solver** — n_grid / grid_lim / substep_dt / frame_dt / frame_num / FLIP-PIC ratio / RPIC damping / grid v damping.
- **Forces** — gravity (vec3 widget).
- **Sim setup** — sim bounds (6 inputs grouped as min/max per axis), mpm_space_viewpoint_center (vec3), mpm_space_vertical_upward_axis (vec3 dropdown: X / Y / Z).
- **Camera** — init azimuth/elevation/radius, deltas, move-camera toggle, default cam idx.
- **Particle filling** — n_grid / max_particles_num / density_threshold (nested dict expanded).
- **Other** — opacity_threshold, show_hint.
- **Boundary conditions** — visual list editor. Each row = one BC. Click `+ Add boundary` → dropdown of valid types → row appears with type-specific form fields.
  - `bounding_box`: no fields (just the type)
  - `surface_collider`: position (vec3), normal (vec3), surface_type (dropdown), friction (slider 0–1)
  - `cuboid`: center (vec3), size (vec3), velocity (vec3), start_time (s), end_time (s)
  - `release_particles_sequentially`: order (dropdown: x / y / z), start_time (s), interval (s)
  - Drag handles to reorder. Click trash icon to delete. Inline error if a required field is empty.
- **Provenance** (read-only footer) — "Based on `jelly` · 4 edits". Click → shows a diff view (preset → current edits, side by side).

Below the recipe sections:
- **Save preset** input + button (saves the current effective recipe to `work/_user_recipes/<name>.json` and adds to Outliner).
- **Particles** slider (the one runtime override that doesn't live in the recipe JSON itself).

### Status strip (bottom)

Fixed 32px bar. Left-to-right: transport (Play / Pause), progress bar, frame counter (`42/150`), ETA, separator, `⌘K` hint, `console ▾` accordion (expands to show last 200 stdout lines, monospace, auto-scroll-to-bottom).

## Recipe authoring system

This is what hurts most about the current workbench. MVP ships four targeted fixes:

### 1. Visual BC editor (replaces raw JSON)

Boundary conditions are no longer a JSON textarea. The Properties pane renders them as a typed list, one row per BC, with a dropdown for the type and a per-type schema-driven form. The schema lives in TypeScript and is also written to `core/boundary_schema.json` so any future client (CLI, vkgs, scripts) can render the same forms.

### 2. Material auto-fill on change

Picking a different material from the dropdown triggers a snap of all material-related params (E, ν, density, yield_stress, friction_angle, β, ξ, hardening, α₀, plastic_viscosity) to validated defaults for that material. Defaults come from a `core/material_defaults.json` keyed by material name, derived from the existing `R7_diversity` configs (which are already known-good for the cluster_6_15 reference building).

The snap fires a one-shot toast at the bottom of the screen: `Snapped to metal defaults — Undo`. Undo restores the previous values exactly. Skips if the user has already manually edited a material param (avoids destroying user work).

### 3. Provenance trail

Every recipe carries `_provenance` metadata (added invisibly to the JSON):

```json
{
  "_provenance": {
    "based_on": "jelly",
    "based_on_path": "tools/recipes/jelly.json",
    "saved_at": "2026-05-07T14:23:12Z",
    "diff_keys": ["E", "g", "n_grid"]
  }
}
```

Properties pane footer shows "Based on `jelly` · 4 edits" with a click-through to a diff view (two columns: preset values vs current values, edited keys highlighted).

### 4. Run = preset + model + diff (auto-save)

Every Run automatically writes to `work/fused/<run_name>/`:

- `frames/frame_*.ply` (existing fused output)
- `recipe_effective.json` (the exact merged config that drove this run)
- `manifest.json` (model path, started_at, finished_at, exit code, particles, frame count, host/GPU info)

Loading any past run from the Outliner restores: viewport plays the frames, Properties pane loads `recipe_effective.json`, model badge shows the source ply. "Re-run with these inputs" is one click.

## Tech stack

### Frontend

| Concern | Choice | Why |
|---|---|---|
| Framework | React 18 + TypeScript + Vite | Industry-standard for the look we want |
| Styling | Tailwind CSS + shadcn/ui (Radix primitives under the hood) | Component ownership, premium polish, no library lock-in |
| Layout | `react-resizable-panels` | Drag-resizable splits with `localStorage` persistence |
| 3D | `@react-three/fiber` + `@react-three/drei` | Best-in-class React + Three.js bridge |
| Splat rendering | `@mkkellogg/gaussian-splats-3d` | Mature 3DGS renderer; supports animation; falls back to point cloud |
| Command palette | `cmdk` | Same primitive Linear, Vercel, and Cursor use |
| Client state | `zustand` | Minimal, well-suited to ephemeral UI state |
| Server state | `@tanstack/react-query` | Standard for REST/WebSocket polling + cache |
| Forms | `react-hook-form` + `zod` schemas | Type-safe validation including the BC schemas |
| Icons | `lucide-react` | Clean, consistent, professional |

### Backend

| Concern | Choice | Why |
|---|---|---|
| Server | FastAPI + uvicorn | Matches our Python sim core, async-native |
| Comms | WebSocket (binary frames) + REST (CRUD) | Real-time frame streaming + standard config endpoints |
| Sim launcher | subprocess wrapping the existing `sim_one.sh` | Zero changes to the sim core |
| File storage | Local filesystem under `work/` | Matches existing layout |
| Auth | None in MVP (localhost only) | Phase 2 if we ever expose remotely |

### Bridge protocol

WebSocket events (server → client):

```typescript
type ServerEvent =
  | { type: "sim_status",  state: "idle" | "running" | "done" | "error",
      run_id: string, started_at: number, finished_at?: number }
  | { type: "sim_progress", run_id: string, stage: string,
      n_frames: int, total_frames: int, fps_observed: number }
  | { type: "frame", run_id: string, frame_idx: int,
      xyz_binary: ArrayBuffer }   // (N, 3) float32
  | { type: "log", run_id: string, line: string }
```

REST endpoints (key ones):

```
POST   /api/runs                  start a run; body = { model, recipe, particles, output_name }
DELETE /api/runs/:id              cancel
GET    /api/runs                  list
GET    /api/runs/:id              metadata + manifest
GET    /api/runs/:id/frames/:i    raw frame ply (server reads from disk)
GET    /api/recipes               list builtin + user
POST   /api/recipes/:name         save user preset
GET    /api/models                list (history + uploaded)
POST   /api/models/upload         multipart upload .ply
WS     /api/stream                events from above
```

### Distribution

```bash
pip install gsfluent           # installs sim core + frontend static assets
gsfluent serve                 # starts FastAPI + opens browser at http://localhost:8080
gsfluent run-sim ...           # CLI fallback (existing sim_one.sh path, headless)
```

The Vite build outputs `dist/` static assets, which are bundled into the Python wheel and served by FastAPI at `/`. No node toolchain on user machines.

## Runtime architecture

```
                    ┌────────────────────────────────────────────────┐
                    │         Browser (single tab)                   │
                    │   React + R3F + zustand + react-query          │
                    └────────────────┬─────────────────┬─────────────┘
                                     │ WebSocket        │ REST
                                     │ (frames, status) │ (CRUD)
                    ┌────────────────▼─────────────────▼─────────────┐
                    │            FastAPI (uvicorn)                   │
                    │  - run launcher: subprocess sim_one.sh         │
                    │  - frame watcher: tails work/fused/<run>/      │
                    │  - recipe + model CRUD                         │
                    │  - serves Vite static assets at /              │
                    └────────────────┬───────────────────────────────┘
                                     │ subprocess + filesystem
                    ┌────────────────▼───────────────────────────────┐
                    │       Existing sim core (untouched)            │
                    │  sim_one.sh → gs_simulation_building.py        │
                    │  Taichi / Warp / PyTorch / diff_gaussian_rast  │
                    └────────────────────────────────────────────────┘
```

**Frame streaming.** A FileSystemWatcher in the FastAPI backend watches `work/fused/<run>/` for new `frame_*.ply`. On each new frame, it parses the ply, extracts xyz, and sends a binary WebSocket message. Static attrs (covariances, RGB, opacities) are sent once per run on the first frame. The frontend mutates `splat.centers` per frame via R3F refs — no React re-render of the scene tree.

**Backpressure.** If the browser falls behind, the backend skips frame events older than 500ms, sending only the latest. Sim progress (counts) keeps flowing.

**Reconnect.** WebSocket reconnect on disconnect (zustand-managed connection state); REST endpoints + the `manifest.json` on disk make state recovery trivial.

## MVP scope vs Phase 2

### MVP (this spec)

- Single workspace: `Sim`. Compare/Render/Recipes appear in the tab bar but are placeholder "(coming soon)" tabs.
- Three-zone Blender layout with drag-resize splits.
- Glass top bar with model + recipe breadcrumb + Run + status badge.
- Status strip with transport, progress, ETA, console accordion.
- All recipe params from the current viser version + visual BC editor + material auto-fill + provenance trail + run-recipe co-save.
- Model: drag-drop or click-upload `.ply`, plus pick from history.
- Outliner with Models / Recipes / History trees.
- Live frame streaming via WebSocket, R3F + Gaussian Splats rendering.
- `Cmd+K` command palette: load model, load recipe, run, cancel, save preset, switch workspace.
- `i` shortcut for inspector toggle, `Cmd+Enter` for Run, `Cmd+B` for sidebar toggle.

### Phase 1.5

- Live CFL/stability validation (hits this only after we've measured how often the solver-side errors actually catch problems).
- Toast system polish; better error surfacing from the sim subprocess.
- Recipe diff view fully implemented (MVP ships the metadata; the UI for browsing diffs is Phase 1.5).

### Phase 2

- `Compare` workspace: split view of 2–4 past runs scrubbing in sync.
- `Render` workspace: configure camera path + export polished mp4 via headless Blender or vkgs.
- `Recipes` workspace: full preset management (rename, delete, share, import, JSON editor).
- Multi-cell sim support in the viewport (cluster grids).
- Tauri desktop wrap with native menus + system file picker.

### Phase 3

- Full Blender-style draggable area splits + merging (a panel can host any editor type, not just swap between fixed types).
- Multi-user / shared sessions.
- Cloud-backed recipe sharing.

## Risks + open questions

1. **`@mkkellogg/gaussian-splats-3d` animation API.** I'm assuming live `centers` updates work cleanly per frame at 200k splats. Need to validate in a 1-day spike before committing to it. Fallback: roll our own R3F splat renderer using the same WebGL technique viser uses today.
2. **Frame ply parsing in JS.** Each frame is ~3–8 MB; parsing 200k splats client-side per frame may be too slow on a laptop. Mitigation: backend pre-converts ply → packed binary `Float32Array` and ships only xyz deltas (12 bytes/splat × 200k = 2.4 MB/frame) rather than full plys.
3. **Static asset size.** A polished React bundle with R3F + a splat renderer + shadcn components could land around 800 KB–1.5 MB gzipped. Acceptable but worth monitoring.
4. **Recipe schema drift.** Two sources of truth (`core/material_defaults.json`, `core/boundary_schema.json`) need to stay in sync with the Python sim core. Lock it via a smoke test: parse every shipped recipe at CI time, fail if any key is missing from the schema.
5. **viser deprecation timing.** The current `tools/workbench.py` keeps working during the rebuild. We deprecate it the moment the React build hits feature parity (probably end of week 3). Keep both around in `tools/` for one release after.

## Acceptance criteria for MVP

- A casual user, given just the URL, can: drag a `.ply` into the viewport → pick `jelly` from the recipe dropdown → click Run → watch the building wobble. Zero sliders touched. **Time-to-first-result ≤ 90s** (kernel JIT included).
- A power user can: load a past run from History → it restores model + effective recipe → tweak two params → Save preset → Run → see the new run in History within 10 seconds of completion.
- Drag-resize any panel divider works; the layout persists across reloads.
- `Cmd+K` opens the command palette; every primary action is reachable via keyboard.
- The build is one command: `pip install gsfluent && gsfluent serve`. No node toolchain required for users.
