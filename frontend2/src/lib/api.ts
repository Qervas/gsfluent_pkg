/**
 * Hand-typed API client. Mirrors apps/api/src/gsfluent_api/schemas.py.
 *
 * Replaced by openapi-typescript-generated client in a follow-up once
 * the openapi.json export pipeline lands (plan task 1.8 -> CI -> commit).
 */

const BASE = ""; // same-origin via Caddy / vite proxy

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(`${r.status} ${r.statusText}: ${text}`);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// ---------- types ----------

export type SubCheck = { ok: boolean; error?: string } & Record<string, unknown>;

export type Health = {
  status: "ok" | "degraded";
  version: string;
  postgres: SubCheck;
  redis: SubCheck;
  minio: SubCheck;
  gpu: SubCheck;
};

export type SystemConfig = {
  max_concurrent_sims: number;
  max_concurrent_renders: number;
  version: string;
  git_sha: string;
};

export type RunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ModelEntity = {
  id: string;
  name: string;
  minio_path: string;
  size_bytes: number;
  num_gaussians: number | null;
  source_metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type Recipe = {
  id: string;
  name: string;
  content: Record<string, unknown>;
  version: number;
  starred: boolean;
  created_at: string;
  updated_at: string;
};

export type Run = {
  id: string;
  name: string;
  status: RunStatus;
  model_id: string;
  recipe_id: string | null;
  recipe_snapshot: Record<string, unknown>;
  worker_id: string | null;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  gpu_seconds: number;
  peak_vram_bytes: number;
  error: string | null;
  created_at: string;
};

export type Artifact = {
  id: string;
  run_id: string;
  kind: "cell" | "log" | "video" | "preview" | "manifest";
  frame_idx: number | null;
  minio_path: string;
  size_bytes: number;
  created_at: string;
};

export type Page<T> = { items: T[]; next_cursor: string | null };

// ---------- endpoints ----------

export const api = {
  system: {
    health: () => http<Health>("/v1/system/health"),
    config: () => http<SystemConfig>("/v1/system/config"),
    setConfig: (body: Partial<Pick<SystemConfig, "max_concurrent_sims" | "max_concurrent_renders">>) =>
      http<SystemConfig>("/v1/system/config", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },
  models: {
    list: (cursor?: string) =>
      http<Page<ModelEntity>>(`/v1/models${cursor ? `?cursor=${cursor}` : ""}`),
    get: (id: string) => http<ModelEntity>(`/v1/models/${id}`),
    delete: (id: string) =>
      http<void>(`/v1/models/${id}`, { method: "DELETE" }),
  },
  recipes: {
    list: (cursor?: string) =>
      http<Page<Recipe>>(`/v1/recipes${cursor ? `?cursor=${cursor}` : ""}`),
    get: (id: string) => http<Recipe>(`/v1/recipes/${id}`),
    create: (body: { name: string; content: Record<string, unknown> }) =>
      http<Recipe>("/v1/recipes", { method: "POST", body: JSON.stringify(body) }),
    patch: (id: string, body: Partial<Pick<Recipe, "name" | "content" | "starred">>) =>
      http<Recipe>(`/v1/recipes/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    delete: (id: string) =>
      http<void>(`/v1/recipes/${id}`, { method: "DELETE" }),
  },
  runs: {
    list: (params?: { status?: RunStatus; model_id?: string; cursor?: string }) => {
      const q = new URLSearchParams();
      if (params?.status) q.set("status", params.status);
      if (params?.model_id) q.set("model_id", params.model_id);
      if (params?.cursor) q.set("cursor", params.cursor);
      const qs = q.toString();
      return http<Page<Run>>(`/v1/runs${qs ? `?${qs}` : ""}`);
    },
    get: (id: string) => http<Run>(`/v1/runs/${id}`),
    artifacts: (id: string) => http<Artifact[]>(`/v1/runs/${id}/artifacts`),
    submit: (body: {
      name: string;
      model_id: string;
      recipe_id?: string;
      recipe_inline?: Record<string, unknown>;
    }) =>
      http<Run>("/v1/runs", { method: "POST", body: JSON.stringify(body) }),
    cancel: (id: string) =>
      http<void>(`/v1/runs/${id}/cancel`, { method: "POST" }),
  },
};
