import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { SequenceItem } from "@/lib/types";

/** "683k", "1.2M", "—" for null/undefined. Cheap, no Intl roundtrip. */
function formatSplats(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(0)     + "k";
  return String(n);
}

/** "12s", "3m", "4h", "2d", "3w" — single-component relative time.
 * Returns null for missing/unparseable input. */
function formatRelativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60)       return `${Math.floor(secs)}s`;
  if (secs < 3600)     return `${Math.floor(secs / 60)}m`;
  if (secs < 86400)    return `${Math.floor(secs / 3600)}h`;
  if (secs < 604800)   return `${Math.floor(secs / 86400)}d`;
  return `${Math.floor(secs / 604800)}w`;
}

/** Outliner list of playable sequences. Source is /api/sequences,
 * mirrored locally by tools/sync_daemon.py. New sequences from sim
 * runs on the server arrive here automatically; no import affordance
 * needed in the typical client-server flow. */
export function SequenceTree({
  onPick,
}: {
  onPick: (run_name: string) => void;
}) {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });
  const simRunName = useStore((s) => s.simRunName);

  // Two-step delete: first click arms the row, second click confirms.
  const [armed, setArmed] = useState<string | null>(null);
  const del = useMutation({
    mutationFn: (name: string) => api.sequences.delete(name),
    onSuccess: (_, name) => {
      qc.invalidateQueries({ queryKey: ["sequences"] });
      qc.invalidateQueries({ queryKey: ["history"] });
      const st = useStore.getState();
      if (st.simRunName === name) {
        // Reset playback state for the now-deleted active sequence.
        // One atomic set so subscribers never see an intermediate
        // state with simState="running" + simRunName=null.
        useStore.setState({
          simRunName: null,
          simState: "idle",
          simNFrames: 0,
          simLog: [],
          staticAttrs: null,
          frameXyz: new Map(),
          currentFrameIdx: 0,
          simStage: "idle",
          simStartedAt: null,
          simFirstFrameAt: null,
          simLastLogAt: null,
          sceneFloor: 0,
        });
      }
      setArmed(null);
    },
    onError: (err: unknown) => {
      // eslint-disable-next-line no-console
      console.error("[SequenceTree] delete failed:", err);
      setArmed(null);
    },
  });

  // Newest first by created_at. Backend already sorts this way; we
  // re-sort here for defense (server might serve in any order, and
  // it's cheap to be idempotent).
  const visible: SequenceItem[] = useMemo(() => {
    const xs = [...(data as SequenceItem[])];
    xs.sort((a, b) => {
      const ta = a.created_at ? Date.parse(a.created_at) : 0;
      const tb = b.created_at ? Date.parse(b.created_at) : 0;
      return tb - ta;
    });
    return xs;
  }, [data]);

  return (
    <div>
      <div className="flex items-center px-2 py-1 mt-2">
        <span className="text-text-muted text-[10px] uppercase tracking-wider flex-1">
          Sequences
        </span>
      </div>

      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {!isLoading && visible.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1">
          (no sequences — run a sim on the server)
        </div>
      )}
      <div className="space-y-1 px-1">
        {visible.map((s: SequenceItem) => {
          const isActive = simRunName === s.name;
          const isArmed = armed === s.name;
          const age = formatRelativeTime(s.created_at);
          // Provenance is the killer metadata: model + recipe + age.
          // model_ref comes straight from _meta.json (server writes it
          // in runner._write_sequence_meta). When missing (imported
          // sequences), we just show source.
          const model = s.model_ref ?? null;
          const recipeFromName = extractRecipeHint(s.name, model);
          return (
            <button
              key={s.name}
              type="button"
              onClick={() => {
                setArmed(null);
                onPick(s.name);
              }}
              className={
                "group relative w-full text-left rounded-md p-2 " +
                "transition-colors duration-fast " +
                (isActive
                  ? "bg-accent/10 ring-1 ring-accent/40"
                  : "hover:bg-elevated/60")
              }
              title={
                s.source_path
                  ? `${s.name}\nsource: ${s.source_path}`
                  : s.name
              }
              aria-current={isActive ? "true" : undefined}
            >
              {/* Active accent strip on the left edge. */}
              {isActive && (
                <span
                  className="absolute left-0 top-2 bottom-2 w-0.5 rounded-r bg-accent shadow-accent-glow-soft"
                  aria-hidden
                />
              )}

              {/* Top row: name + broken indicator + delete */}
              <div className="flex items-center gap-1.5">
                <span
                  className={
                    "flex-1 truncate text-xs font-medium " +
                    (isActive ? "text-accent" : "text-text-primary")
                  }
                >
                  {s.name}
                </span>
                {s.is_broken && (
                  <AlertTriangle
                    size={11}
                    className="shrink-0 text-warning"
                    aria-label="source folder is missing"
                  />
                )}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (isArmed) {
                      del.mutate(s.name);
                    } else {
                      setArmed(s.name);
                    }
                  }}
                  disabled={del.isPending}
                  className={
                    "shrink-0 px-1 py-0.5 rounded transition-opacity duration-fast " +
                    (isArmed
                      ? "text-error bg-error/15 opacity-100"
                      : "text-text-muted opacity-0 group-hover:opacity-100 hover:text-error hover:bg-error/10")
                  }
                  title={
                    isArmed
                      ? "Click again to confirm delete"
                      : "Delete sequence"
                  }
                  aria-label="delete sequence"
                >
                  {isArmed ? (
                    <span className="text-xxs uppercase tracking-wider px-1">
                      delete?
                    </span>
                  ) : (
                    <X size={11} />
                  )}
                </button>
              </div>

              {/* Bottom row: provenance badges. model · recipe · counts · age */}
              <div className="flex items-center gap-1.5 mt-0.5 text-xxs text-text-muted font-mono tabular-nums">
                {model && (
                  <span
                    className="truncate"
                    title={`model: ${model}`}
                  >
                    {model}
                  </span>
                )}
                {model && recipeFromName && <span aria-hidden>·</span>}
                {recipeFromName && (
                  <span
                    className="truncate"
                    title={`recipe (guessed from name): ${recipeFromName}`}
                  >
                    {recipeFromName}
                  </span>
                )}
                <span className="ml-auto flex items-center gap-1.5 shrink-0">
                  <span title={`${s.frame_count} frames at ${s.fps_hint} fps`}>
                    {s.frame_count}f
                  </span>
                  {s.n_splats != null && (
                    <>
                      <span aria-hidden>·</span>
                      <span title={`${s.n_splats.toLocaleString()} splats`}>
                        {formatSplats(s.n_splats)}
                      </span>
                    </>
                  )}
                  {age && (
                    <>
                      <span aria-hidden>·</span>
                      <span title={s.created_at ?? ""}>{age}</span>
                    </>
                  )}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** Best-effort: pull the recipe name out of the sequence name. Sim
 *  runs are named `<model>_<recipe>_<timestamp>` by the workbench;
 *  imports use whatever the user chose. Returns null if we can't
 *  identify a recipe segment confidently. */
function extractRecipeHint(name: string, model: string | null): string | null {
  if (!model) return null;
  if (!name.startsWith(model + "_")) return null;
  const rest = name.slice(model.length + 1);
  // Drop the trailing timestamp segment (matches our YYYYMMDDTHHMMSS shape).
  const stripped = rest.replace(/_\d{8,15}$/, "");
  return stripped || null;
}
