import type {
  Recipe,
  RecipeListItem,
  ModelItem,
  HistoryEntry,
  RunStatus,
  SequenceItem,
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
    delete: (n: string) =>
      fetch(`/api/recipes/${encodeURIComponent(n)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
  },
  models: {
    list: () => fetch("/api/models").then(j<ModelItem[]>),
    upload: (ply: File, camerasJson?: File) => {
      const fd = new FormData();
      fd.append("ply", ply);
      if (camerasJson) fd.append("cameras_json", camerasJson);
      return fetch("/api/models/upload", { method: "POST", body: fd }).then(j<ModelItem>);
    },
    register: (path: string) =>
      fetch("/api/models/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      }).then(j<ModelItem>),
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
    deleteHistory: (run_name: string) =>
      fetch(`/api/runs/history/${encodeURIComponent(run_name)}`, {
        method: "DELETE",
      }).then(j<{ deleted: string }>),
  },
  schemas: {
    boundaries: () => fetch("/api/schemas/boundaries").then(j<BCSchemas>),
    materials:  () => fetch("/api/schemas/materials").then(j<MaterialDefaults>),
  },
  sequences: {
    list: () => fetch("/api/sequences").then(j<SequenceItem[]>),
    import: (folder_path: string, name?: string) =>
      fetch("/api/sequences/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_path, name }),
      }).then(j<SequenceItem>),
    delete: (name: string) =>
      fetch(`/api/sequences/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
  },
};
