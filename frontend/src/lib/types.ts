export type Workspace = "sim" | "recipes";

export type Recipe = {
  name: string;
  source: "builtin" | "user";
  data: Record<string, unknown>;
};
export type RecipeListItem = { name: string; source: "builtin" | "user" };
export type ModelItem = {
  name: string;
  path: string;
  // Phase 4: present when the model was rewritten Y-up -> Z-up at
  // import time. Audit-only; the workbench never branches on this.
  converted_from?: "y-up" | null;
  // /api/models/register may report whether convert_y_up forced an
  // import-by-copy ("copied-and-converted") vs the no-copy default
  // ("registered"). Optional because /api/models list responses don't
  // carry a mode.
  mode?: "registered" | "copied-and-converted";
};
export type RunState = "queued" | "running" | "done" | "error" | "cancelled";
export type RunStatus = { id: string; name: string; state: RunState };

export type HistoryEntry = {
  run_name: string;
  status: string;
  started_at: number;
  finished_at?: number;
  particles?: number;
  recipe_source?: string;
};

// Library sequence — both sim-produced (source="sim") and externally
// imported (source="import") variants share this shape. Mirrors the
// dict returned by /api/sequences and /api/sequences/import.
export type SequenceItem = {
  name: string;
  source: "sim" | "import" | string;
  source_path?: string | null;
  model_ref?: string | null;
  frame_count: number;
  fps_hint: number;
  n_splats: number | null;
  coord_convention: "z-up";
  first_frame_full: boolean;
  is_broken: boolean;
  created_at: string | null;
  // Phase 4: present when frames were rewritten Y-up -> Z-up at
  // import time (frames/ is materialized rather than symlinked).
  converted_from?: "y-up" | null;
  // Server-side cache descriptor. Always present in the response —
  // individual field values may be null when the corresponding artifact
  // hasn't been built (run pack_splats.py / pack_sequence.py on server).
  // Used by the sync daemon for staleness detection and surfaced in the
  // outliner as size/synced indicators. Field names mirror the server's
  // api/sequences.py exactly: splats_gsq_* for the Splats-mode cache,
  // frames_bin_* for the Points-mode cache.
  cache: {
    splats_gsq_mtime: number | null;
    splats_gsq_bytes: number | null;
    frames_bin_mtime: number | null;
    frames_bin_bytes: number | null;
  };
};

export type BCFieldSpec = {
  name: string;
  type: "vec3" | "float" | "int" | "string";
  default: unknown;
  hint: string;
};
export type BCSchemas = Record<string, BCFieldSpec[]>;
export type MaterialDefaults = Record<string, Record<string, number>>;

// --- Structured composer (material x scenario x building -> recipe) ---------
// Summaries returned by GET /api/compose/library to populate the picker
// dropdowns. They carry only what the UI needs, not the recipe internals.
export type MaterialSummary = {
  name: string;
  material: string;
  E: number;
  nu: number;
  density: number;
  yield_stress: number;
  friction_angle: number;
  desc: string;
};
export type ScenarioSummary = {
  name: string;
  base: string;                       // "pinned" | "driven" | "free"
  frame_num: number;
  gravity: number;
  recommended_material: string | null;
  damping: number | null;
  num_events: number;
  desc: string;
};
export type BuildingSummary = {
  name: string;
  model_path: string;
  bbox: number[];
  sim_area: number[];
  desc: string;
};
export type ComposeLibrary = {
  materials: MaterialSummary[];
  scenarios: ScenarioSummary[];
  buildings: BuildingSummary[];
};
export type ComposeResult = {
  material: string;
  scenario: string;
  building: string;
  recipe_data: Record<string, unknown>;
};

// Sequence metadata published into the store by SplatScene. The frame cursor
// itself lives in SplatScene's rAF loop (not React) — only the total count is
// surfaced, to drive PlaybackBar visibility + StatusPanel. Set once on load.
export type PlaybackState = {
  n_frames: number;
};

// Diagnostics — one row per moving part of the split-topology dev stack.
// Surfaced through the StatusPill in the top bar so the user sees which
// piece is down when something silently breaks. Shapes mirror what each
// endpoint actually returns; null fields mean "not applicable / not yet
// reported."
export type BackendHealth = { status: string; pkg_root: string };

export type DiagPart = {
  ok: boolean;
  detail?: string;        // short human-readable status (e.g. "last sync 4s ago")
  error?: string;         // present when ok=false
};

export type DiagSnapshot = {
  backend: DiagPart;
};
