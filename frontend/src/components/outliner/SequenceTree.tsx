import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowDownUp, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { SequenceItem } from "@/lib/types";

// Persisted in localStorage so the user's preference survives a reload.
type SortKey = "date" | "name" | "size";
const SORT_KEY_LS = "gsfluent.outliner.sortKey";
const DEFAULT_SORT: SortKey = "date";

/** Read the persisted sort choice, defaulting cleanly if unknown. */
function loadSortKey(): SortKey {
  try {
    const v = localStorage.getItem(SORT_KEY_LS);
    if (v === "date" || v === "name" || v === "size") return v;
  } catch { /* localStorage unavailable */ }
  return DEFAULT_SORT;
}

/** "683k", "1.2M", "—" for null/undefined. Cheap, no Intl roundtrip. */
function formatSplats(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(0)     + "k";
  return String(n);
}

/** "12s", "3m", "4h", "2d", "3w" — single-letter suffix, single-component
 * relative time. Returns null for missing/unparseable input. */
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

/** "Size" key for the sort=size option. Returns a tuple (cohort, value)
 * where cohort=0 means cached (real bytes from cache.viser_npz_bytes)
 * and cohort=1 means uncached (synthetic splat × frame proxy). Mixing
 * the two on one number axis means a sequence's sort position would
 * jump discontinuously the moment its cache builds; bucketing by
 * cohort first keeps the within-cohort ordering stable. */
function sequenceSortKey(s: SequenceItem): [number, number] {
  const cb = s.cache?.viser_npz_bytes;
  if (typeof cb === "number") return [0, cb];
  if (s.n_splats != null) return [1, s.n_splats * (s.frame_count || 1)];
  return [1, 0];
}

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
  const [sortKey, setSortKey] = useState<SortKey>(loadSortKey);
  const [filter, setFilter] = useState("");

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
        // Reset playback state for the now-deleted active sequence.
        // Previous code did `resetForNewRun("")` (which sets
        // simState="running") followed by `setSimState("idle")` —
        // two separate set() calls, leaving an intermediate frame
        // where subscribers saw simState=running with empty
        // simRunName. Collapse to one atomic set.
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

  // Cycle through sortKey options. Three options, single button —
  // simpler than a dropdown for an outliner header. Tooltip says where
  // the next click lands.
  const cycleSort = () => {
    const next: SortKey = sortKey === "date" ? "name" : sortKey === "name" ? "size" : "date";
    setSortKey(next);
    try { localStorage.setItem(SORT_KEY_LS, next); } catch { /* ignore */ }
  };

  // Filter + sort. Memoized so a typing user doesn't churn the diff
  // on unrelated state changes (delete-armed, busy, etc.).
  const visible: SequenceItem[] = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let xs = data as SequenceItem[];
    if (q) xs = xs.filter((s) => s.name.toLowerCase().includes(q));
    const sorted = [...xs];
    if (sortKey === "name") {
      sorted.sort((a, b) => a.name.localeCompare(b.name));
    } else if (sortKey === "size") {
      sorted.sort((a, b) => {
        const [ca, va] = sequenceSortKey(a);
        const [cb, vb] = sequenceSortKey(b);
        if (ca !== cb) return ca - cb;          // cached cohort first
        return vb - va;                          // largest first within cohort
      });
    } else {
      // "date" — newest first. Backend already sorts this way; we re-do
      // it here so the after-filter result is correctly ordered.
      sorted.sort((a, b) => {
        const ta = a.created_at ? Date.parse(a.created_at) : 0;
        const tb = b.created_at ? Date.parse(b.created_at) : 0;
        return tb - ta;
      });
    }
    return sorted;
  }, [data, filter, sortKey]);

  return (
    <div>
      <div className="flex items-center px-2 py-1 mt-2">
        <span className="text-text-muted text-[10px] uppercase tracking-wider flex-1">
          Sequences{filter && ` · ${visible.length}/${data.length}`}
        </span>
        <button
          onClick={cycleSort}
          title={`Sort: ${sortKey}. Click for next (date → name → size).`}
          className="flex items-center gap-0.5 text-[10px] uppercase tracking-wider text-text-muted hover:text-text-primary px-1"
        >
          <ArrowDownUp size={10} />
          <span>{sortKey}</span>
        </button>
      </div>

      {/* Search + import inputs share a column so the outliner header
          stays narrow. Search clears with Esc; empty input restores the
          full list without losing the user's sort choice. */}
      <div className="px-2 pb-1 flex gap-1 items-center">
        <Search size={11} className="text-text-muted shrink-0" />
        <input
          type="text"
          placeholder="filter…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") setFilter(""); }}
          className="font-mono flex-1 bg-canvas border border-border rounded px-1.5 py-0.5 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
        />
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
      {!isLoading && data.length > 0 && visible.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1 italic">
          no sequences match “{filter}”
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
            {/* Metadata badges: frame count + splat count + relative
                age. Each column is shrink-0 so a long sequence name
                truncates instead of pushing the badges off-screen. */}
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
