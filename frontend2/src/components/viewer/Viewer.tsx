import { useNavigate, useSearch } from "@tanstack/react-router";
import { ViewerLocal } from "./ViewerLocal";
import { ViewerServer } from "./ViewerServer";
import { cn } from "@/lib/cn";

type Mode = "server" | "local";

export function Viewer({
  runId, modelId,
}: { runId?: string; modelId?: string }): JSX.Element {
  // Persist mode + pause in the URL so back/forward + share-a-link work.
  const search = useSearch({ strict: false }) as { render?: string; paused?: string };
  const navigate = useNavigate();

  const mode: Mode = search.render === "local" ? "local" : "server";
  const paused = search.paused === "1";

  const setMode = (m: Mode) => {
    navigate({
      to: ".",
      search: (prev) => ({ ...prev, render: m === "server" ? undefined : "local" }),
      replace: true,
    });
  };
  const setPaused = (p: boolean) => {
    navigate({
      to: ".",
      search: (prev) => ({ ...prev, paused: p ? "1" : undefined }),
      replace: true,
    });
  };

  const target = runId ? { run_id: runId } : modelId ? { model_id: modelId } : null;

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="inline-flex rounded-full bg-elevated/60 p-0.5 border border-border text-xs">
          {(["server", "local"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "px-3 py-1 rounded-full transition-colors",
                mode === m
                  ? "bg-accent text-slate-950 font-semibold"
                  : "text-slate-400 hover:text-slate-200",
              )}
            >
              {m === "server" ? "Server (WebRTC)" : "Local (WebGL)"}
            </button>
          ))}
        </div>
        {mode === "server" && (
          <button
            type="button"
            onClick={() => setPaused(!paused)}
            className="px-2 py-1 rounded text-xs text-slate-400 hover:text-slate-100 hover:bg-elevated/60"
          >
            {paused ? "▶ resume" : "⏸ pause"}
          </button>
        )}
      </div>

      {mode === "server"
        ? <ViewerServer target={target} enabled={!paused} />
        : <ViewerLocal runId={runId} modelId={modelId} />}
    </section>
  );
}
