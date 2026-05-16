import type {
  Recipe,
  RecipeListItem,
  ModelItem,
  HistoryEntry,
  RunStatus,
  SequenceItem,
  BCSchemas,
  MaterialDefaults,
  BackendHealth,
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
    upload: (ply: File, camerasJson?: File, convertYUp?: boolean) => {
      const fd = new FormData();
      fd.append("ply", ply);
      if (camerasJson) fd.append("cameras_json", camerasJson);
      // FastAPI Form(bool) coerces the "true"/"false" string. Only
      // append when truthy to keep the multipart body small for the
      // overwhelming default-off case.
      if (convertYUp) fd.append("convert_y_up", "true");
      return fetch("/api/models/upload", { method: "POST", body: fd }).then(j<ModelItem>);
    },
    register: (path: string, convertYUp?: boolean) =>
      fetch("/api/models/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, convert_y_up: !!convertYUp }),
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
  diag: {
    // Backend reachability probe. The vite proxy decides which actual
    // host this hits (local uvicorn or tunneled server); from the
    // workbench's perspective, "can we talk to /api/*?" is the signal.
    health: () => fetch("/api/health").then(j<BackendHealth>),
  },
  sequences: {
    list: () => fetch("/api/sequences").then(j<SequenceItem[]>),
    import: (folder_path: string, name?: string, convertYUp?: boolean) =>
      fetch("/api/sequences/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_path, name, convert_y_up: !!convertYUp }),
      }).then(j<SequenceItem>),
    uploadNpz: (npz: File, name?: string) => {
      const fd = new FormData();
      fd.append("file", npz);
      if (name) fd.append("name", name);
      return fetch("/api/sequences/upload-npz", {
        method: "POST",
        body: fd,
      }).then(j<SequenceItem>);
    },
    delete: (name: string) =>
      fetch(`/api/sequences/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
  },
};
