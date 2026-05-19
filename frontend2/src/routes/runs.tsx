import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, type RunStatus } from "@/lib/api";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/runs")({
  component: RunsPage,
});

const STATUS_TONE: Record<RunStatus, string> = {
  queued: "bg-slate-500/20 text-slate-300",
  running: "bg-cyan-500/20 text-cyan-300",
  completed: "bg-emerald-500/20 text-emerald-300",
  failed: "bg-red-500/20 text-red-300",
  cancelled: "bg-amber-500/20 text-amber-300",
};

function RunsPage(): JSX.Element {
  const q = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs.list(),
    refetchInterval: 5_000,
  });

  if (q.isLoading) {
    return <p className="text-slate-400">Loading runs…</p>;
  }
  if (q.error) {
    return (
      <p className="text-red-400">
        Failed to load runs: {(q.error as Error).message}
      </p>
    );
  }
  const runs = q.data?.items ?? [];

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Runs</h1>
        <span className="text-xs text-slate-500">{runs.length} shown</span>
      </header>

      {runs.length === 0 ? (
        <div className="glass p-8 text-center text-slate-400">
          No runs yet. Submit one from <strong>New run</strong>.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-slate-500 border-b border-border">
              <th className="py-2 font-medium">Name</th>
              <th className="font-medium">Status</th>
              <th className="font-medium">Queued</th>
              <th className="font-medium">Worker</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-b border-border/40 hover:bg-elevated/40">
                <td className="py-2 font-mono text-xs">{r.name}</td>
                <td>
                  <span className={cn("pill", STATUS_TONE[r.status])}>{r.status}</span>
                </td>
                <td className="text-slate-400 text-xs">{r.queued_at.slice(0, 19)}</td>
                <td className="text-slate-400 text-xs">{r.worker_id ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
