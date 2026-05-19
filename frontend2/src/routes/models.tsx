import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ModelEntity } from "@/lib/api";

export const Route = createFileRoute("/models")({
  component: ModelsPage,
});

async function uploadFile(file: File): Promise<ModelEntity> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/v1/models", { method: "POST", body: form });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return (await r.json()) as ModelEntity;
}

function DropZone({ onUploaded }: { onUploaded: () => void }): JSX.Element {
  const [progress, setProgress] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    for (const f of Array.from(files)) {
      setProgress(`uploading ${f.name} (${(f.size / 1e6).toFixed(1)} MB)…`);
      try {
        await uploadFile(f);
      } catch (e) {
        setError(String(e));
        setProgress(null);
        return;
      }
    }
    setProgress(null);
    setError(null);
    onUploaded();
  }

  return (
    <div
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        void handleFiles(e.dataTransfer.files);
      }}
      className="glass p-6 border-2 border-dashed border-border/60 text-center"
    >
      <p className="text-sm text-slate-400">Drop a .ply file here, or</p>
      <label className="inline-block mt-2 px-3 py-1.5 rounded bg-elevated/80 hover:bg-elevated text-sm cursor-pointer">
        choose file
        <input
          type="file"
          accept=".ply"
          className="hidden"
          onChange={(e) => void handleFiles(e.currentTarget.files)}
        />
      </label>
      {progress && <p className="text-xs text-cyan-300 mt-2">{progress}</p>}
      {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
    </div>
  );
}

function ModelsPage(): JSX.Element {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["models"], queryFn: () => api.models.list() });

  const del = useMutation({
    mutationFn: (mid: string) => api.models.delete(mid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
  });

  const models = q.data?.items ?? [];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Models</h1>
        <p className="text-xs text-slate-500">3DGS assets uploaded to MinIO.</p>
      </header>

      <DropZone onUploaded={() => qc.invalidateQueries({ queryKey: ["models"] })} />

      {q.isLoading && <p className="text-slate-400">Loading…</p>}

      {models.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {models.map((m) => (
            <div key={m.id} className="glass p-3 space-y-2">
              <h3 className="font-mono text-sm truncate">{m.name}</h3>
              <p className="text-xs text-slate-500">
                {m.num_gaussians ? `${(m.num_gaussians / 1e6).toFixed(2)} M splats` : "splat count unknown"} ·{" "}
                {(m.size_bytes / 1e6).toFixed(1)} MB
              </p>
              <div className="flex justify-end pt-1">
                <button
                  type="button"
                  onClick={() => del.mutate(m.id)}
                  disabled={del.isPending}
                  className="text-xs text-red-400 hover:text-red-300"
                >
                  delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
