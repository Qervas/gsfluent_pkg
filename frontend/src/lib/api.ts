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
  ComposeLibrary,
  ComposeResult,
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

/** Absolute URL of a sequence's .gsq cache (download-then-play artifact).
 *  Same base resolution as every other API call (VITE_BACKEND_URL or
 *  same-origin). */
export function splatsGsqUrl(name: string): string {
  return apiUrl(`/api/sequences/${encodeURIComponent(name)}/cache/splats.gsq`);
}

/** Absolute URL streaming a model's highest-iteration point_cloud.ply.
 *  The endpoint takes the model's on-disk path (allowlist-checked server-side);
 *  callers resolve name→path via the models list. */
export function modelPlyUrl(path: string): string {
  return apiUrl(`/api/models/file?path=${encodeURIComponent(path)}`);
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

/** Warm the TCP/TLS handshake to the backend by injecting a
 * `<link rel="preconnect">` head element. Idempotent — re-calls during
 * one page session are no-ops. Use from outliner hover handlers so a
 * subsequent click pays only the request-response RTT, not the
 * full handshake (saves 50-300 ms on a cold connection).
 *
 * Same-origin (no VITE_BACKEND_URL) → no-op: the browser is already
 * connected to its own origin. Only the cross-origin static-host
 * deployment benefits, but the cost of calling this in the same-origin
 * case is just a no-op early return. */
let _preconnected = false;
export function preconnectBackend(): void {
  if (_preconnected) return;
  if (!BACKEND_URL) return;          // same-origin, nothing to warm
  if (typeof document === "undefined") return; // SSR safety
  const link = document.createElement("link");
  link.rel = "preconnect";
  link.href = BACKEND_URL;
  // crossorigin matters when the backend serves no-credential GETs —
  // the browser dedupes preconnect by (href, crossorigin), so the
  // attribute MUST match what the subsequent fetch will send.
  link.crossOrigin = "anonymous";
  document.head.appendChild(link);
  _preconnected = true;
}

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
    /** Apply an in-place orientation transform to a stored model. Returns the
     *  updated model meta (new sha256 → cache-bust the splat fetch). */
    reorient: (n: string, transform: "y_up_to_z_up" | "flip_180") =>
      f(`/api/models/${encodeURIComponent(n)}/reorient`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transform }),
      }).then(j<ModelItem>),
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
  // Structured recipe composition. `library` populates the picker dropdowns;
  // `run` turns a (material, scenario, building) pick into a flat recipe the
  // sim eats. An over-ceiling / unknown pick comes back as a 422 whose
  // detail.error.message says why — surface it; don't swallow it.
  compose: {
    library: () => f("/api/compose/library").then(j<ComposeLibrary>),
    run: (material: string, scenario: string, building: string) =>
      f("/api/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ material, scenario, building }),
      }).then(j<ComposeResult>),
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
    delete: (name: string) =>
      f(`/api/sequences/${encodeURIComponent(name)}`, { method: "DELETE" }).then(
        j<{ deleted: string }>,
      ),
    // On-demand .gsq cache build. The client POSTs build to kick off
    // pack_splats.py server-side (idempotent — fast "done" if the .gsq
    // already exists), then polls buildStatus until done/error. See
    // server/gsfluent/api/sequences.py.
    buildCache: (name: string) =>
      f(`/api/sequences/${encodeURIComponent(name)}/cache/build`, {
        method: "POST",
      }).then(j<CacheBuildStatus>),
    buildStatus: (name: string) =>
      f(`/api/sequences/${encodeURIComponent(name)}/cache/build-status`).then(
        j<CacheBuildStatus>,
      ),
  },
};

/** Server-side .gsq cache build job state. */
export type CacheBuildStatus = {
  name: string;
  state: "idle" | "building" | "done" | "error";
  error?: string | null;
};
