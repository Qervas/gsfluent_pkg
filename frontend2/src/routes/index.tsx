import { Link, createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, type Run } from "@/lib/api";
import { Viewer } from "@/components/viewer/Viewer";
import { cn } from "@/lib/cn";

// Landing page IS the viewport. Picks the most recent completed run
// (the one most likely to have viewable frames) and mounts the viewer
// directly. A small scene-picker bar above it lets you switch.

export const Route = createFileRoute("/")({
  component: HomePage,
});

function HomePage(): JSX.Element {
  const runsQ = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.runs.list(),
    refetchInterval: 10_000,
  });

  const runs = runsQ.data?.items ?? [];
  // Most recent completed run first, then most recent of any kind.
  const completed = runs.filter((r) => r.status === "completed");
  const featured: Run | undefined = completed[0] ?? runs[0];

  if (runsQ.isLoading) {
    return <p className="text-slate-400">Loading…</p>;
  }

  if (!featured) {
    return (
      <div className="space-y-4">
        <header>
          <h1 className="text-xl font-semibold">Viewer</h1>
          <p className="text-xs text-slate-500">
            No runs yet — submit one to see splats here.
          </p>
        </header>
        <div className="glass p-12 text-center space-y-3">
          <p className="text-slate-400">Nothing to render.</p>
          <Link
            to="/sim/new"
            className="inline-block px-4 py-2 rounded bg-accent text-slate-950 text-sm font-semibold"
          >
            + New run
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <ScenePicker runs={runs} active={featured} />
      <Viewer runId={featured.id} />
    </div>
  );
}

function ScenePicker({ runs, active }: { runs: Run[]; active: Run }): JSX.Element {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <span className="text-xs uppercase tracking-wider text-slate-500">scene</span>
      <select
        value={active.id}
        onChange={(e) => {
          // Use plain anchor navigation so we land on the run-detail page
          // (which has the same viewer + the per-run log/artifacts).
          window.location.href = `/runs/${e.currentTarget.value}`;
        }}
        className="bg-elevated/60 border border-border rounded px-2 py-1.5 text-sm font-mono"
      >
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name} · {r.status}
          </option>
        ))}
      </select>
      <span
        className={cn(
          "pill text-xs",
          active.status === "completed"
            ? "bg-emerald-500/20 text-emerald-300"
            : "bg-slate-500/20 text-slate-300",
        )}
      >
        {active.status}
      </span>
      <span className="text-xs text-slate-500 font-mono">{active.id.slice(0, 8)}</span>
      <div className="flex-1" />
      <Link
        to="/runs/$id"
        params={{ id: active.id }}
        className="text-xs text-accent hover:underline"
      >
        open detail →
      </Link>
    </div>
  );
}
