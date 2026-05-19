import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const Route = createFileRoute("/models")({
  component: ModelsPage,
});

function ModelsPage(): JSX.Element {
  const q = useQuery({ queryKey: ["models"], queryFn: () => api.models.list() });
  const models = q.data?.items ?? [];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Models</h1>
        <p className="text-xs text-slate-500">3DGS assets. Upload UI lands in Phase 7.</p>
      </header>

      {q.isLoading && <p className="text-slate-400">Loading…</p>}
      {q.error && <p className="text-red-400">Error: {(q.error as Error).message}</p>}

      {models.length === 0 ? (
        <div className="glass p-8 text-center text-slate-400">No models yet.</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {models.map((m) => (
            <div key={m.id} className="glass p-3 space-y-1">
              <h3 className="font-mono text-sm truncate">{m.name}</h3>
              <p className="text-xs text-slate-500">
                {m.num_gaussians ? `${(m.num_gaussians / 1e6).toFixed(2)} M splats` : "splat count unknown"} ·{" "}
                {(m.size_bytes / 1e6).toFixed(1)} MB
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
