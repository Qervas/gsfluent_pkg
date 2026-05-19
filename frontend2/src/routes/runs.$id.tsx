import { createFileRoute, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type RunStatus } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useStream } from "@/hooks/useStream";

export const Route = createFileRoute("/runs/$id")({
  component: RunDetailPage,
});

const STATUS_TONE: Record<RunStatus, string> = {
  queued: "bg-slate-500/20 text-slate-300",
  running: "bg-cyan-500/20 text-cyan-300",
  completed: "bg-emerald-500/20 text-emerald-300",
  failed: "bg-red-500/20 text-red-300",
  cancelled: "bg-amber-500/20 text-amber-300",
};

function RunDetailPage(): JSX.Element {
  const { id } = useParams({ from: "/runs/$id" });
  const qc = useQueryClient();

  const run = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.runs.get(id),
    refetchInterval: 5_000,
  });
  const artifacts = useQuery({
    queryKey: ["run", id, "artifacts"],
    queryFn: () => api.runs.artifacts(id),
    refetchInterval: 5_000,
  });

  // Live: invalidate on any event for this run.
  useStream([`events:runs:${id}`], () => {
    qc.invalidateQueries({ queryKey: ["run", id] });
    qc.invalidateQueries({ queryKey: ["run", id, "artifacts"] });
  });

  // Live log lines.
  const [logLines, setLogLines] = useState<string[]>([]);
  useStream([`events:logs:${id}`], (e) => {
    if (e.type === "log.line") {
      const line = `[${(e.level as string)?.toUpperCase?.() ?? "INFO"}] ${e.message as string}`;
      setLogLines((prev) => [...prev.slice(-1000), line]);
    }
  });

  // Reset logs on id change.
  useEffect(() => setLogLines([]), [id]);

  const cancel = useMutation({
    mutationFn: () => api.runs.cancel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["run", id] }),
  });

  if (run.isLoading) return <p className="text-slate-400">Loading…</p>;
  if (run.error) return <p className="text-red-400">{(run.error as Error).message}</p>;
  if (!run.data) return <p className="text-slate-400">Not found.</p>;

  const r = run.data;
  const isLive = r.status === "queued" || r.status === "running";

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold font-mono">{r.name}</h1>
          <p className="text-xs text-slate-500">id: {r.id}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={cn("pill", STATUS_TONE[r.status])}>{r.status}</span>
          {isLive && (
            <button
              type="button"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
              className="px-3 py-1.5 rounded bg-red-500/80 hover:bg-red-500 text-white text-xs font-semibold"
            >
              {cancel.isPending ? "cancelling…" : "cancel"}
            </button>
          )}
        </div>
      </header>

      <section className="glass p-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <Stat label="model" value={r.model_id.slice(0, 8)} />
        <Stat label="recipe" value={r.recipe_id ? r.recipe_id.slice(0, 8) : "inline"} />
        <Stat label="queued" value={r.queued_at.slice(0, 19)} />
        <Stat label="worker" value={r.worker_id ?? "—"} />
        <Stat label="started" value={r.started_at?.slice(0, 19) ?? "—"} />
        <Stat label="completed" value={r.completed_at?.slice(0, 19) ?? "—"} />
        <Stat label="gpu_seconds" value={String(r.gpu_seconds)} />
        <Stat label="peak_vram" value={`${(r.peak_vram_bytes / 1e9).toFixed(2)} GB`} />
      </section>

      {r.error && (
        <section className="glass p-4 border-l-2 border-red-500">
          <h2 className="text-sm font-semibold mb-1">Error</h2>
          <pre className="text-xs text-red-300 whitespace-pre-wrap">{r.error}</pre>
        </section>
      )}

      <section className="glass p-4">
        <h2 className="text-sm font-semibold mb-2">
          Artifacts ({artifacts.data?.length ?? 0})
        </h2>
        {artifacts.data && artifacts.data.length > 0 ? (
          <ul className="text-xs font-mono space-y-1 max-h-64 overflow-y-auto">
            {artifacts.data.map((a) => (
              <li key={a.id} className="flex gap-3">
                <span className="text-slate-500 w-12">{a.kind}</span>
                <span className="text-slate-500 w-8">
                  {a.frame_idx != null ? `#${a.frame_idx}` : ""}
                </span>
                <span className="flex-1 truncate">{a.minio_path}</span>
                <span className="text-slate-500">{(a.size_bytes / 1024).toFixed(1)} KB</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500 text-xs">No artifacts yet.</p>
        )}
      </section>

      <section className="glass p-4">
        <h2 className="text-sm font-semibold mb-2">Live log</h2>
        <pre className="text-xs font-mono bg-slate-900/60 p-3 rounded max-h-80 overflow-y-auto">
          {logLines.length > 0 ? logLines.join("\n") : "(no log lines yet)"}
        </pre>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div>
      <div className="text-slate-500 uppercase tracking-wider text-[10px]">{label}</div>
      <div className="font-mono text-slate-200 truncate">{value}</div>
    </div>
  );
}
