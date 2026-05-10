export type Workspace = "sim" | "compare" | "render" | "recipes";

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
};

export type StaticAttrs = {
  n: number;
  R: Float32Array;       // (n, 3, 3)
  scales: Float32Array;  // (n, 3)
  rgb: Float32Array;     // (n, 3) in [0,1]
  opacity: Float32Array; // (n,)
};

export type FrameMeta = { run_name: string; frame_idx: number; n: number };

export type BCFieldSpec = {
  name: string;
  type: "vec3" | "float" | "string";
  default: unknown;
  hint: string;
};
export type BCSchemas = Record<string, BCFieldSpec[]>;
export type MaterialDefaults = Record<string, Record<string, number>>;
