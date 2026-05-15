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
      {visible.map((s: SequenceItem) => {
        const isActive = simRunName === s.name;
        const isArmed = armed === s.name;
        const sourceColor =
          s.source === "import" ? "text-accent" : "text-text-muted";
        const age = formatRelativeTime(s.created_at);
        return (
          <div
            key={s.name}
            className="group w-full flex items-center px-3 py-1 text-xs hover:bg-elevated"
          >
            <button
              onClick={() => {
                setArmed(null);
                onPick(s.name);
              }}
              className={
                "flex-1 text-left truncate " +
                (isActive ? "text-accent" : "text-text-primary")
              }
              title={
                s.source_path
                  ? `${s.name}\nsource: ${s.source_path}`
                  : s.name
              }
            >
              <span className="truncate">{s.name}</span>
            </button>
            {s.is_broken && (
              <span
                className="shrink-0 text-warning mx-1"
                title="source folder is missing — re-link or delete"
              >
                <AlertTriangle size={11} />
              </span>
            )}
            <span
              className={"shrink-0 text-[10px] mx-2 " + sourceColor}
              title={s.source === "import" ? "imported folder" : "produced by a sim run"}
            >
              {s.source}
            </span>
            {/* Metadata badges: frame count + splat count + age.
                Each column is shrink-0 so a long name truncates
                instead of pushing the badges off-screen. */}
            <span
              className="shrink-0 text-[10px] text-text-muted mr-2 tabular-nums"
              title={`${s.frame_count} frames at ${s.fps_hint} fps hint`}
            >
              {s.frame_count}f
            </span>
            {s.n_splats != null && (
              <span
                className="shrink-0 text-[10px] text-text-muted mr-2 tabular-nums"
                title={`${s.n_splats.toLocaleString()} splats`}
              >
                {formatSplats(s.n_splats)}
              </span>
            )}
            {age && (
              <span
                className="shrink-0 text-[10px] text-text-muted mr-2 tabular-nums"
                title={s.created_at ?? ""}
              >
                {age}
              </span>
            )}
            <button
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
                "shrink-0 px-1 rounded transition-colors " +
                (isArmed
                  ? "text-error bg-error/10 hover:bg-error/20"
                  : "text-text-muted opacity-0 group-hover:opacity-100 hover:text-error hover:bg-elevated")
              }
              title={
                isArmed
                  ? "Click again to confirm delete"
                  : s.source === "import"
                  ? "Remove the library entry (source folder is preserved)"
                  : "Delete this sequence from disk"
              }
            >
              {isArmed ? (
                <span className="text-[10px] uppercase tracking-wider px-1">
                  delete?
                </span>
              ) : (
                <X size={11} />
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
