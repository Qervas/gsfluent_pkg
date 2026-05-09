import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { SequenceItem } from "@/lib/types";

export function HistoryTree({ onPick }: { onPick: (run_name: string) => void }) {
  const qc = useQueryClient();
  // Phase 2 migration: history === sim-produced sequences. We read from
  // /api/sequences (single source of truth) and filter source==="sim",
  // mapping into the HistoryEntry shape the rest of this component
  // expects so the dirty-hunks rendering keeps working unchanged.
  const { data: sequences = [], isLoading } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });
  const data = useMemo(
    () =>
      (sequences as SequenceItem[])
        .filter((s) => s.source === "sim")
        .map((s) => ({
          run_name: s.name,
          // Sequences have no per-run lifecycle status today — Phase 1
          // dropped the manifest.json:status field from the unified list.
          // Default to "done" for completed sequences.
          status: "done",
          particles: s.n_splats ?? undefined,
        })),
    [sequences],
  );

  // Two-step delete: first click arms a single row, second click confirms.
  // Click any other row (or the same row's main label) to disarm. Less
  // friction than a modal but still impossible to fat-finger.
  const [armed, setArmed] = useState<string | null>(null);

  const del = useMutation({
    mutationFn: (run_name: string) => api.sequences.delete(run_name),
    onSuccess: (_, run_name) => {
      qc.invalidateQueries({ queryKey: ["sequences"] });
      qc.invalidateQueries({ queryKey: ["history"] });
      // If the deleted run was the active one, clear playback state so
      // the viewport doesn't keep showing frames from a now-vanished dir.
      const st = useStore.getState();
      if (st.simRunName === run_name) {
        st.resetForNewRun(""); // resets sim state + clears frames
        st.setSimState("idle");
      }
      setArmed(null);
    },
    onError: (err: unknown) => {
      // eslint-disable-next-line no-console
      console.error("[HistoryTree] delete failed:", err);
      setArmed(null);
    },
  });

  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1 mt-2">
        History
      </div>
      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {!isLoading && data.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1">(no runs yet)</div>
      )}
      {data.map((h) => {
        const isArmed = armed === h.run_name;
        return (
          <div
            key={h.run_name}
            className="group w-full flex items-center px-3 py-1 text-xs hover:bg-elevated text-text-primary"
          >
            <button
              onClick={() => {
                setArmed(null);
                onPick(h.run_name);
              }}
              className="flex-1 text-left truncate"
              title={`${h.run_name} · status=${h.status} · particles=${h.particles ?? "?"}`}
            >
              <span className="truncate">{h.run_name}</span>
            </button>
            <span
              className={
                "shrink-0 text-[10px] mx-2 " +
                (h.status === "done"
                  ? "text-success"
                  : h.status === "error"
                  ? "text-error"
                  : h.status === "cancelled"
                  ? "text-text-muted"
                  : "text-accent")
              }
            >
              {h.status}
            </span>
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (isArmed) {
                  del.mutate(h.run_name);
                } else {
                  setArmed(h.run_name);
                }
              }}
              disabled={del.isPending}
              className={
                "shrink-0 px-1 rounded transition-colors " +
                (isArmed
                  ? "text-error bg-error/10 hover:bg-error/20"
                  : "text-text-muted opacity-0 group-hover:opacity-100 hover:text-error hover:bg-elevated")
              }
              title={isArmed ? "Click again to confirm delete" : "Delete this run from disk"}
            >
              {isArmed ? (
                <span className="text-[10px] uppercase tracking-wider px-1">delete?</span>
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
