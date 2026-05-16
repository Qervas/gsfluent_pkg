import { useEffect, useMemo, useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Play, Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
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

  const activeModel = useStore((s) => s.activeModel);
  const simRunName  = useStore((s) => s.simRunName);

  const [open, setOpen] = useState<Record<string, boolean>>(() => loadTreeState());
  useEffect(() => { persistTreeState(open); }, [open]);

  const toggle = useCallback((modelName: string) => {
    setOpen((s) => ({ ...s, [modelName]: !s[modelName] }));
  }, []);

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
        const isActiveModel = activeModel?.name === m.name;
        return (
          <div key={m.name}>
            <button
              type="button"
              onClick={() => onPickModel(m)}
              className={
                "w-full flex items-center gap-1 px-3 py-1 text-left hover:bg-elevated " +
                (isActiveModel ? "text-accent" : "text-text-primary")
              }
            >
              <span
                onClick={(e) => { e.stopPropagation(); toggle(m.name); }}
                className="text-text-muted cursor-pointer p-0.5"
                aria-label={isExpanded ? "Collapse" : "Expand"}
              >
                {isExpanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              </span>
              <span className="font-mono truncate flex-1">{m.name}</span>
              <span className="text-text-muted text-[10px]">
                {childSeqs.length || ""}
              </span>
            </button>
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
                    return (
                      <button
                        key={s.name}
                        type="button"
                        onClick={() => onLoadRun(s.name)}
                        className={
                          "w-full flex items-center gap-1 py-1 text-left hover:bg-elevated rounded " +
                          (simRunName === s.name ? "text-accent" : "text-text-secondary")
                        }
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
          {sequencesByModel.orphans.map((s) => (
            <button
              key={s.name}
              type="button"
              onClick={() => onLoadRun(s.name)}
              className={
                "w-full flex items-center gap-1 px-3 py-1 text-left hover:bg-elevated " +
                (simRunName === s.name ? "text-accent" : "text-text-secondary")
              }
            >
              <span className="font-mono text-[11px] truncate flex-1">{s.name}</span>
              <span className="text-[9px] px-1 rounded bg-elevated text-text-muted">
                imported
              </span>
            </button>
          ))}
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
