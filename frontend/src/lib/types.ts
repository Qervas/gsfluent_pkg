export type Recipe = {
  name: string;
  source: "builtin" | "user";
  data: Record<string, unknown>;
};
export type RecipeListItem = { name: string; source: "builtin" | "user" };
export type ModelItem = { name: string; path: string };
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
