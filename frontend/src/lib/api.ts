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

/** Optional build-time override of the API host.
 *
 * Unset by default — the SPA fires `/api/*` requests at its own origin
 * and the vite preview/dev server proxies them to the configured
 * `GSFLUENT_BACKEND_URL` (set when launching). Override at build time
 * by setting `VITE_BACKEND_URL=http://host:port` in `.env.local` /
 * `.env.production`, in which case the bundle hits that host directly
 * (skipping the proxy — needed for prebuilt static deploys without a
 * preview server in front). Trailing slash is stripped to match path
 * concatenation. */
const BACKEND_URL = ((import.meta.env.VITE_BACKEND_URL as string | undefined) ?? "")
  .replace(/\/$/, "");

function apiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  if (!BACKEND_URL) return path;
  return BACKEND_URL + path;
}

const j = async <T>(r: Response): Promise<T> => {
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`HTTP ${r.status}: ${text || r.statusText}`);
  }
  return r.json();
};

const f: typeof fetch = (input, init) => {
  const url = typeof input === "string" ? apiUrl(input) : input;
  return fetch(url, init);
};

// Gzip a File/Blob in the browser via the Compression Streams API
// (Chromium 80+, Firefox 113+, Safari 16.4+). Used by the model upload
// path to shrink transport size — .ply 3DGS files compress 2-3x with
// no visual loss since they're plain text/binary numeric arrays.
async function gzipFile(file: File): Promise<Blob> {
  const stream = file.stream().pipeThrough(new CompressionStream("gzip"));
  return new Response(stream).blob();
}

export const api = {
  recipes: {
    list: () => f("/api/recipes").then(j<RecipeListItem[]>),
    get:  (n: string) => f(`/api/recipes/${encodeURIComponent(n)}`).then(j<Recipe>),
    save: (n: string, data: Record<string, unknown>, based_on?: string) =>
      f(`/api/recipes/${encodeURIComponent(n)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, based_on }),
      }).then(j<Recipe>),
    delete: (n: string) =>
      f(`/api/recipes/${encodeURIComponent(n)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
  },
  models: {
    list: () => f("/api/models").then(j<ModelItem[]>),
    checkHash: (sha256: string, filename?: string) =>
      f("/api/models/check_hash", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sha256, filename }),
      }).then(
        j<{
          exists: boolean;
          name?: string;
          path?: string;
          n_splats?: number;
        }>,
      ),
    upload: async (
      ply: File,
      camerasJson?: File,
      convertYUp?: boolean,
      opts?: {
        onProgress?: (loaded: number, total: number) => void;
        onPhase?: (phase: "compressing" | "uploading") => void;
        signal?: AbortSignal;
      },
    ): Promise<ModelItem> => {
      // Gzip the ply blob client-side via the Compression Streams API.
      // The server accepts a `ply_encoding` form field rather than the
      // HTTP-level Content-Encoding header because FastAPI doesn't
      // auto-decompress request bodies (intercepting raw multipart is
      // much more invasive than just gzipping the field's bytes).
      opts?.onPhase?.("compressing");
      const gzippedPly = await gzipFile(ply);

      const fd = new FormData();
      // Preserve original filename so the server can derive the model
      // base name. The blob's MIME type is irrelevant for our handler.
      fd.append("ply", gzippedPly, ply.name);
      fd.append("ply_encoding", "gzip");
      if (camerasJson) fd.append("cameras_json", camerasJson);
      // FastAPI Form(bool) coerces the "true"/"false" string. Only
      // append when truthy to keep the multipart body small for the
      // overwhelming default-off case.
      if (convertYUp) fd.append("convert_y_up", "true");

      return new Promise<ModelItem>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", apiUrl("/api/models/upload"));
        opts?.onPhase?.("uploading");
        if (opts?.signal) {
          opts.signal.addEventListener("abort", () => xhr.abort());
        }
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && opts?.onProgress) {
            opts.onProgress(e.loaded, e.total);
          }
        };
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              resolve(JSON.parse(xhr.responseText));
            } catch {
              reject(new Error("invalid JSON response"));
            }
          } else {
            reject(
              new Error(
                `HTTP ${xhr.status}: ${xhr.responseText || xhr.statusText}`,
              ),
            );
          }
        };
        xhr.onerror = () => reject(new Error("network error"));
        xhr.onabort = () => reject(new Error("upload aborted"));
        xhr.send(fd);
      });
    },
    register: (path: string, convertYUp?: boolean) =>
      f("/api/models/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, convert_y_up: !!convertYUp }),
      }).then(j<ModelItem>),
    delete: (n: string) =>
      f(`/api/models/${encodeURIComponent(n)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
  },
  runs: {
    list:    () => f("/api/runs").then(j<RunStatus[]>),
    history: () => f("/api/runs/history").then(j<HistoryEntry[]>),
    start: (req: {
      run_name: string;
      model_path: string;
      recipe_data: Record<string, unknown>;
      recipe_source: string;
      particles: number;
    }) =>
      f("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      }).then(j<{ run_id: string; run_name: string }>),
    cancel: (id: string) =>
      f(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }).then(
        j<{ status: string }>,
      ),
    deleteHistory: (run_name: string) =>
      f(`/api/runs/history/${encodeURIComponent(run_name)}`, {
        method: "DELETE",
      }).then(j<{ deleted: string }>),
    log: (run_name: string, offset: number) =>
      f(
        `/api/runs/${encodeURIComponent(run_name)}/log?offset=${offset}`,
      ).then(j<{ content: string; offset: number; size: number }>),
  },
  schemas: {
    boundaries: () => f("/api/schemas/boundaries").then(j<BCSchemas>),
    materials:  () => f("/api/schemas/materials").then(j<MaterialDefaults>),
  },
  diag: {
    // Backend reachability probe. The vite proxy decides which actual
    // host this hits (local uvicorn or tunneled server); from the
    // workbench's perspective, "can we talk to /api/*?" is the signal.
    health: () => f("/api/health").then(j<BackendHealth>),
  },
  sequences: {
    list: () => f("/api/sequences").then(j<SequenceItem[]>),
    import: (folder_path: string, name?: string, convertYUp?: boolean) =>
      f("/api/sequences/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder_path, name, convert_y_up: !!convertYUp }),
      }).then(j<SequenceItem>),
    uploadNpz: (npz: File, name?: string) => {
      const fd = new FormData();
      fd.append("file", npz);
      if (name) fd.append("name", name);
      return f("/api/sequences/upload-npz", {
        method: "POST",
        body: fd,
      }).then(j<SequenceItem>);
    },
    delete: (name: string) =>
      f(`/api/sequences/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
  },
};
