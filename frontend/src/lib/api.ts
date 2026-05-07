import type {
  Recipe,
  RecipeListItem,
  ModelItem,
  HistoryEntry,
  RunStatus,
  BCSchemas,
  MaterialDefaults,
} from "./types";

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${text || r.statusText}`);
  }
  return r.json();
};

export const api = {
  recipes: {
    list: () => fetch("/api/recipes").then(j<RecipeListItem[]>),
    get:  (n: string) => fetch(`/api/recipes/${encodeURIComponent(n)}`).then(j<Recipe>),
    save: (n: string, data: Record<string, unknown>, based_on?: string) =>
      fetch(`/api/recipes/${encodeURIComponent(n)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, based_on }),
      }).then(j<Recipe>),
  },
  models: {
    list: () => fetch("/api/models").then(j<ModelItem[]>),
    upload: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return fetch("/api/models/upload", { method: "POST", body: fd }).then(j<ModelItem>);
    },
  },
  runs: {
    list:    () => fetch("/api/runs").then(j<RunStatus[]>),
    history: () => fetch("/api/runs/history").then(j<HistoryEntry[]>),
    start: (req: {
      run_name: string;
      model_path: string;
      recipe_data: Record<string, unknown>;
      recipe_source: string;
      particles: number;
    }) =>
      fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      }).then(j<{ run_id: string; run_name: string }>),
    cancel: (id: string) =>
      fetch(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }).then(
        j<{ status: string }>,
      ),
  },
  schemas: {
    boundaries: () => fetch("/api/schemas/boundaries").then(j<BCSchemas>),
    materials:  () => fetch("/api/schemas/materials").then(j<MaterialDefaults>),
  },
};
