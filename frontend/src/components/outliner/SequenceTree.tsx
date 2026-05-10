import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { SequenceItem } from "@/lib/types";

// Sequences = sim-produced + externally-imported playable frame folders.
// This component supersedes the Phase-1 history tree at the data level —
// the same backend list is sourced here, with a path-paste import row at
// the top mirroring ModelTree's "register external path" affordance.
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

  const [path, setPath] = useState("");
  const [convertYUp, setConvertYUp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const onImport = async () => {
    const p = path.trim();
    if (!p) return;
    setBusy(true);
    setImportError(null);
    try {
      const seq = await api.sequences.import(p, undefined, convertYUp);
      qc.invalidateQueries({ queryKey: ["sequences"] });
      setPath("");
      // Make the freshly-imported sequence the active replay target.
      onPick(seq.name);
    } catch (e) {
      setImportError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // Two-step delete (matches HistoryTree pattern): first click arms a
  // single row, second click confirms. Keeps the UI close to what users
  // already have muscle memory for, avoids modal overhead.
  const [armed, setArmed] = useState<string | null>(null);
  const del = useMutation({
    mutationFn: (name: string) => api.sequences.delete(name),
    onSuccess: (_, name) => {
      qc.invalidateQueries({ queryKey: ["sequences"] });
      qc.invalidateQueries({ queryKey: ["history"] });
      const st = useStore.getState();
      if (st.simRunName === name) {
        st.resetForNewRun("");
        st.setSimState("idle");
      }
      setArmed(null);
    },
    onError: (err: unknown) => {
      // eslint-disable-next-line no-console
      console.error("[SequenceTree] delete failed:", err);
      setArmed(null);
    },
  });

  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1 mt-2">
        Sequences
      </div>
      <div className="px-2 pb-1 flex gap-1">
        <input
          type="text"
          placeholder="/path/to/frame_folder"
          value={path}
          disabled={busy}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onImport();
          }}
          className="font-mono flex-1 bg-canvas border border-border rounded px-1.5 py-0.5 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
          title="Paste a folder of frame_*.ply files to symlink as a sequence"
        />
        <button
          onClick={onImport}
          disabled={busy || !path.trim()}
          className="bg-elevated hover:bg-border border border-border text-accent text-[11px] px-2 rounded disabled:opacity-30"
          title={
            convertYUp
              ? "Convert + copy frames into the library (rewrites Y-up to Z-up)"
              : "Import the folder as a sequence (no copy)"
          }
        >
          {busy ? "…" : "+"}
        </button>
      </div>
      <div className="px-2 pb-1">
        <label
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer select-none"
          title="Source is Y-up (PhysGaussian/Inria); convert to Z-up at import. Materializes the frames into the library instead of symlinking, so the entry never goes broken."
        >
          <input
            type="checkbox"
            checked={convertYUp}
            disabled={busy}
            onChange={(e) => setConvertYUp(e.target.checked)}
            className="accent-accent"
          />
          Y-up
        </label>
      </div>
      {importError && (
        <div className="px-3 pb-1 text-error text-[10px]">{importError}</div>
      )}
      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {!isLoading && data.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1">
          (no sequences — run a sim or paste a frame folder above)
        </div>
      )}
      {data.map((s: SequenceItem) => {
        const isActive = simRunName === s.name;
        const isArmed = armed === s.name;
        const sourceColor =
          s.source === "import" ? "text-accent" : "text-text-muted";
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
            <span className="shrink-0 text-[10px] text-text-muted mr-2">
              {s.frame_count}f
            </span>
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
