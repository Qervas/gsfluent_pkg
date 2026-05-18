import { useEffect, useMemo, useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Play, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useActiveCell } from "@/lib/use-active-cell";
import type { ModelItem, SequenceItem } from "@/lib/types";

const TREE_STATE_KEY = "gsfluent.source_tree_open";

function loadTreeState(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(TREE_STATE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function persistTreeState(state: Record<string, boolean>) {
  try { localStorage.setItem(TREE_STATE_KEY, JSON.stringify(state)); } catch {}
}

type Props = {
  onPickModel: (m: ModelItem) => void;
  onLoadRun:   (run_name: string) => void;
};

/** Source card — model-rooted hierarchy.
 *
 *  Replaces the old Outliner tabs. Models are parents; sequences hang
 *  underneath their `model_ref` parent. Orphan sequences (model_ref ===
 *  null) get their own group at the bottom. Tree expand/collapse state
 *  per model persists to localStorage.
 */
export function SourceCard({ onPickModel, onLoadRun }: Props) {
  const { data: models = [] } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });
  const { data: sequences = [] } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });

  const { activeCell } = useActiveCell();

  const [open, setOpen] = useState<Record<string, boolean>>(() => loadTreeState());
  useEffect(() => { persistTreeState(open); }, [open]);

  const toggle = useCallback((modelName: string) => {
    setOpen((s) => ({ ...s, [modelName]: !s[modelName] }));
  }, []);

  // Auto-expand the parent model when one of its sequences becomes the
  // active cell — keeps the highlighted row in view without forcing the
  // user to manually click the chevron.
  useEffect(() => {
    if (activeCell?.kind !== "sequence") return;
    const seq = (sequences as SequenceItem[]).find((s) => s.name === activeCell.name);
    if (!seq?.model_ref) return;
    setOpen((s) => (s[seq.model_ref!] ? s : { ...s, [seq.model_ref!]: true }));
  }, [activeCell, sequences]);

  const qc = useQueryClient();
  // Imperative delete handlers. Both confirm before firing.
  const deleteModel = useCallback(
    async (name: string, childCount: number) => {
      const extra = childCount > 0
        ? ` Its ${childCount} sequence${childCount === 1 ? "" : "s"} will be orphaned, not deleted.`
        : "";
      if (!confirm(`Delete model "${name}"?${extra}`)) return;
      try {
        await api.models.delete(name);
        qc.invalidateQueries({ queryKey: ["models"] });
        qc.invalidateQueries({ queryKey: ["sequences"] });
      } catch (e) {
        alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [qc],
  );
  const deleteSequence = useCallback(
    async (name: string) => {
      if (!confirm(`Delete sequence "${name}"?`)) return;
      try {
        await api.sequences.delete(name);
        qc.invalidateQueries({ queryKey: ["sequences"] });
      } catch (e) {
        alert(`Delete failed: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [qc],
  );

  // Group sequences by model_ref. Sequences with null model_ref go to
  // the "orphan" bucket rendered at the bottom.
  const sequencesByModel = useMemo(() => {
    const m: Record<string, SequenceItem[]> = {};
    const orphans: SequenceItem[] = [];
    for (const s of sequences as SequenceItem[]) {
      if (s.model_ref) {
        (m[s.model_ref] ||= []).push(s);
      } else {
        orphans.push(s);
      }
    }
    return { byModel: m, orphans };
  }, [sequences]);

  return (
    <div className="text-xs">
      <div className="px-3 py-2 text-text-muted text-[10px] uppercase tracking-wider">
        Models
      </div>
      {(models as ModelItem[]).map((m) => {
        const isExpanded = open[m.name] ?? false;
        const childSeqs = sequencesByModel.byModel[m.name] ?? [];
        // "Active" here means "this is what the viewport is showing right
        // now" — keyed on activeCell, not activeModel. activeModel can
        // still be the model while a child sequence is the active cell;
        // in that case the model gets a faint "owns the active sequence"
        // hint (text-accent only), while the active row itself gets the
        // full bg + left-stripe treatment.
        const isActiveCell =
          activeCell?.kind === "model" && activeCell.name === m.name;
        const ownsActiveSequence =
          activeCell?.kind === "sequence" &&
          childSeqs.some((s) => s.name === activeCell.name);
        return (
          <div key={m.name}>
            <div
              className={
                "group relative flex items-center gap-1 px-3 py-1 hover:bg-elevated " +
                (isActiveCell
                  ? "bg-accent/15 text-accent before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-accent"
                  : ownsActiveSequence
                  ? "text-accent"
                  : "text-text-primary")
              }
            >
              <button
                type="button"
                onClick={() => toggle(m.name)}
                className="text-text-muted p-0.5 shrink-0"
                aria-label={isExpanded ? "Collapse" : "Expand"}
              >
                {isExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              </button>
              <button
                type="button"
                onClick={() => onPickModel(m)}
                className="flex-1 flex items-center gap-1 text-left min-w-0"
              >
                <span className="font-mono truncate flex-1">{m.name}</span>
                <span className="text-text-muted text-[10px]">
                  {childSeqs.length || ""}
                </span>
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  void deleteModel(m.name, childSeqs.length);
                }}
                title="Delete model"
                className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-error transition-opacity p-0.5 shrink-0"
                aria-label={`Delete model ${m.name}`}
              >
                <Trash2 size={11} />
              </button>
            </div>
            {isExpanded && (
              <div className="pl-6 pr-3">
                {childSeqs.length === 0 ? (
                  <div className="py-1 text-text-muted text-[10px] italic">
                    No runs yet
                  </div>
                ) : (
                  childSeqs.map((s) => {
                    // recipe_source isn't on SequenceItem's declared shape
                    // (the /api/sequences endpoint doesn't surface it),
                    // but the manifest may still carry it for sim-produced
                    // sequences. Pulled defensively so the badge lights up
                    // if/when the backend starts forwarding it.
                    const recipeSource = (s as SequenceItem & { recipe_source?: string }).recipe_source;
                    const isActive =
                      activeCell?.kind === "sequence" && activeCell.name === s.name;
                    return (
                      <div
                        key={s.name}
                        className={
                          "group relative flex items-center gap-1 py-1 px-1 hover:bg-elevated rounded " +
                          (isActive
                            ? "bg-accent/15 text-accent before:absolute before:inset-y-0.5 before:left-[-12px] before:w-0.5 before:bg-accent"
                            : "text-text-secondary")
                        }
                      >
                        <button
                          type="button"
                          onClick={() => onLoadRun(s.name)}
                          className="flex-1 flex items-center gap-1 text-left min-w-0"
                        >
                          <span className="font-mono text-[11px] truncate flex-1">
                            {s.name}
                          </span>
                          {recipeSource && (
                            <span className="text-[9px] px-1 rounded bg-accent/10 text-accent">
                              {recipeSource.replace(/^★ /, "")}
                            </span>
                          )}
                          <Play size={10} className="opacity-50" />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            void deleteSequence(s.name);
                          }}
                          title="Delete sequence"
                          className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-error transition-opacity p-0.5 shrink-0"
                          aria-label={`Delete sequence ${s.name}`}
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    );
                  })
                )}
                <button
                  type="button"
                  onClick={() => onPickModel(m)}
                  className="w-full flex items-center gap-1 mt-1 px-2 py-1 rounded border border-dashed border-accent/30 bg-accent/5 text-accent text-[10px] hover:bg-accent/10"
                >
                  <Plus size={10} />
                  new simulation from {m.name}
                </button>
              </div>
            )}
          </div>
        );
      })}

      {sequencesByModel.orphans.length > 0 && (
        <>
          <div className="px-3 py-2 mt-2 text-text-muted text-[10px] uppercase tracking-wider">
            Orphan sequences
          </div>
          {sequencesByModel.orphans.map((s) => {
            const isActive =
              activeCell?.kind === "sequence" && activeCell.name === s.name;
            return (
            <div
              key={s.name}
              className={
                "group relative flex items-center gap-1 px-3 py-1 hover:bg-elevated " +
                (isActive
                  ? "bg-accent/15 text-accent before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-accent"
                  : "text-text-secondary")
              }
            >
              <button
                type="button"
                onClick={() => onLoadRun(s.name)}
                className="flex-1 flex items-center gap-1 text-left min-w-0"
              >
                <span className="font-mono text-[11px] truncate flex-1">{s.name}</span>
                <span className="text-[9px] px-1 rounded bg-elevated text-text-muted">
                  imported
                </span>
              </button>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  void deleteSequence(s.name);
                }}
                title="Delete sequence"
                className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-error transition-opacity p-0.5 shrink-0"
                aria-label={`Delete sequence ${s.name}`}
              >
                <Trash2 size={11} />
              </button>
            </div>
            );
          })}
        </>
      )}

      <QueryRefresher qc={useQueryClient()} />
    </div>
  );
}

/** Re-fetch sequences once when SourceCard mounts so the tree shows the
 *  freshest data without waiting on the 5s poll. */
function QueryRefresher({ qc }: { qc: ReturnType<typeof useQueryClient> }) {
  useEffect(() => { qc.invalidateQueries({ queryKey: ["sequences"] }); }, [qc]);
  return null;
}
