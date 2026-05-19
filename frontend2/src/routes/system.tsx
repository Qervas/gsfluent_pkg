import { createFileRoute } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type SubCheck } from "@/lib/api";
import { cn } from "@/lib/cn";

export const Route = createFileRoute("/system")({
  component: SystemPage,
});

function CheckRow({ label, sub }: { label: string; sub: SubCheck }): JSX.Element {
  const ok = !!sub.ok;
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/40">
      <span className="font-medium">{label}</span>
      <div className="flex items-center gap-3">
        <span className={cn("pill", ok ? "bg-emerald-500/20 text-emerald-300" : "bg-red-500/20 text-red-300")}>
          {ok ? "ok" : "down"}
        </span>
        {!ok && sub.error && (
          <span className="text-xs text-slate-400 max-w-xs truncate" title={String(sub.error)}>
            {String(sub.error)}
          </span>
        )}
      </div>
    </div>
  );
}

function SystemPage(): JSX.Element {
  const qc = useQueryClient();
  const health = useQuery({
    queryKey: ["system", "health"],
    queryFn: api.system.health,
    refetchInterval: 10_000,
  });
  const config = useQuery({
    queryKey: ["system", "config"],
    queryFn: api.system.config,
  });

  const [sims, setSims] = useState<number | null>(null);
  const [renders, setRenders] = useState<number | null>(null);

  const mutate = useMutation({
    mutationFn: (body: Parameters<typeof api.system.setConfig>[0]) => api.system.setConfig(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["system", "config"] }),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">System</h1>
        <p className="text-xs text-slate-500">Health + concurrency caps. Internal admin.</p>
      </header>

      <section className="glass p-4">
        <h2 className="text-sm font-semibold mb-2">Health</h2>
        {health.isLoading && <p className="text-slate-400">Probing…</p>}
        {health.data && (
          <div>
            <p className="text-xs text-slate-500 mb-2">
              version: <span className="font-mono">{health.data.version}</span>{" "}
              · roll-up:{" "}
              <span className={cn("pill", health.data.status === "ok" ? "bg-emerald-500/20 text-emerald-300" : "bg-amber-500/20 text-amber-300")}>
                {health.data.status}
              </span>
            </p>
            <CheckRow label="postgres" sub={health.data.postgres} />
            <CheckRow label="redis" sub={health.data.redis} />
            <CheckRow label="minio" sub={health.data.minio} />
            <CheckRow label="gpu" sub={health.data.gpu} />
          </div>
        )}
      </section>

      <section className="glass p-4">
        <h2 className="text-sm font-semibold mb-2">Concurrency caps</h2>
        {config.data && (
          <form
            className="flex flex-wrap items-end gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              const body: Parameters<typeof api.system.setConfig>[0] = {};
              if (sims !== null) body.max_concurrent_sims = sims;
              if (renders !== null) body.max_concurrent_renders = renders;
              mutate.mutate(body);
            }}
          >
            <label className="text-xs">
              max sims (current: {config.data.max_concurrent_sims})
              <input
                type="number"
                min={0}
                className="block bg-elevated/60 border border-border rounded px-2 py-1 mt-1 w-32"
                onChange={(e) => setSims(Number(e.currentTarget.value))}
              />
            </label>
            <label className="text-xs">
              max renders (current: {config.data.max_concurrent_renders})
              <input
                type="number"
                min={0}
                className="block bg-elevated/60 border border-border rounded px-2 py-1 mt-1 w-32"
                onChange={(e) => setRenders(Number(e.currentTarget.value))}
              />
            </label>
            <button
              type="submit"
              disabled={mutate.isPending || (sims === null && renders === null)}
              className="px-3 py-1.5 rounded bg-accent text-slate-950 text-xs font-semibold disabled:opacity-40"
            >
              {mutate.isPending ? "saving…" : "save"}
            </button>
            {mutate.error && <span className="text-red-400 text-xs">{(mutate.error as Error).message}</span>}
          </form>
        )}
      </section>
    </div>
  );
}
