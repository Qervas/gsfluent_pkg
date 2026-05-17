import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { Properties } from "@/components/properties/Properties";
import { JsonEditor } from "@/components/properties/widgets/JsonEditor";
import { RunButton } from "@/components/runs/RunButton";
import type { RecipeListItem, SequenceItem } from "@/lib/types";

type Props = {
  subscribe: (run_name: string) => void;
};

/** Simulation card — recipe picker + Form/JSON toggle + params + actions.
 *
 *  State machine (Phase 3 implements the first three; sequence-loaded
 *  read-only summary lands in Phase 6):
 *    - no model selected           → "Pick a model" empty state
 *    - model but no recipe         → recipe picker visible, body hidden
 *    - model + recipe (idle)       → full editor (Form mode for now)
 */
export function SimulationCard({ subscribe }: Props) {
  const activeModel       = useStore((s) => s.activeModel);
  const activeRecipeName  = useStore((s) => s.activeRecipeName);
  const simRunName        = useStore((s) => s.simRunName);
  const simState          = useStore((s) => s.simState);
  const loadActiveRecipe  = useStore((s) => s.loadActiveRecipe);
  const { overrideCount, clearAllOverrides } = useOverrides();
  const [view, setView]   = useState<"form" | "json">(
    () => (localStorage.getItem("gsfluent.sim_view_mode") as "form" | "json") || "form",
  );
  const [saving, setSaving]     = useState(false);
  const [strpError, setStrpError] = useState<string | null>(null);
  const qc = useQueryClient();

  // Confirm before bulk reset only when the user has accumulated enough
  // overrides that an accidental click would lose real work. 3 is the
  // threshold where "I might lose a tweak I forgot about" becomes plausible.
  const CONFIRM_RESET_THRESHOLD = 3;

  const onSaveAsNew = async () => {
    const name = prompt("Save as new recipe — name:");
    if (!name?.trim()) return;
    setSaving(true);
    setStrpError(null);
    // Snapshot effective = {...baseline, ...overrides} at call-start so
    // in-flight slider drags between save→load can't clobber what we're
    // persisting.
    const baseline = useStore.getState().simRecipeBaseline;
    const overrides = useStore.getState().simOverrides;
    const snapshot = JSON.parse(
      JSON.stringify({ ...(baseline ?? {}), ...overrides }),
    );
    try {
      await api.recipes.save(name.trim(), snapshot, activeRecipeName ?? undefined);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      useStore.getState().loadActiveRecipe(name.trim(), snapshot);
    } catch (e) {
      setStrpError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onResetAll = () => {
    if (overrideCount >= CONFIRM_RESET_THRESHOLD) {
      if (!confirm(`Reset ${overrideCount} overrides?`)) return;
    }
    clearAllOverrides();
  };

  const prevSimState = useRef<string>(simState);
  const [showFinishedToast, setShowFinishedToast] = useState(false);

  useEffect(() => {
    if (prevSimState.current === "running" && simState === "done") {
      setShowFinishedToast(true);
      const t = setTimeout(() => setShowFinishedToast(false), 6000);
      prevSimState.current = simState;
      return () => clearTimeout(t);
    }
    prevSimState.current = simState;
  }, [simState]);

  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });

  const isSequenceRun =
    !!simRunName && !simRunName.startsWith("_model:");
  const { data: sequences = [] } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
  });
  const seq = (sequences as SequenceItem[]).find((s) => s.name === simRunName);
  const isOrphan = isSequenceRun && (!seq || seq.model_ref == null);
  const isSequenceUnderModel = isSequenceRun && !isOrphan;
  // SequenceItem doesn't currently declare recipe_source (backend-side
  // change deferred per Phase 2 review); cast for read-only access.
  const seqRecipeSource =
    (seq as unknown as { recipe_source?: string })?.recipe_source;

  const setViewPersist = (v: "form" | "json") => {
    setView(v);
    localStorage.setItem("gsfluent.sim_view_mode", v);
  };

  if (isOrphan) return null;

  if (isSequenceUnderModel) {
    return (
      <div className="px-3 py-3 text-xs space-y-2">
        <div className="text-text-muted text-[10px] uppercase tracking-wider">
          ② Simulation (read-only)
        </div>
        <div className="text-text-secondary">
          Based on recipe{" "}
          <span className="font-mono text-accent">
            {seqRecipeSource ?? "(unknown)"}
          </span>
        </div>
        <div className="text-text-muted text-[10px]">
          This is a finished sequence — params can't be edited.
        </div>
        <button
          type="button"
          onClick={() => {
            const m = useStore.getState().activeModel;
            const rname = seqRecipeSource ?? null;
            if (m && rname) {
              useStore.getState().resetForNewRun(`_model:${m.name}`);
              useStore.getState().setSimState("idle");
              api.recipes.get(rname).then((r) =>
                useStore.getState().loadActiveRecipe(r.name, r.data)
              ).catch(() => {});
            }
          }}
          className="mt-2 w-full px-3 py-1.5 bg-accent/15 text-accent rounded text-[11px] font-medium hover:bg-accent/25"
        >
          New run from this recipe…
        </button>
      </div>
    );
  }

  if (!activeModel) {
    return (
      <div className="px-3 py-4 text-xs text-text-muted text-center">
        Pick a model or sequence to configure simulation.
      </div>
    );
  }

  const onPickRecipe = async (name: string) => {
    if (overrideCount > 0) {
      if (!confirm(`Discard ${overrideCount} override${overrideCount === 1 ? "" : "s"}?`)) return;
    }
    try {
      const r = await api.recipes.get(name);
      loadActiveRecipe(r.name, r.data);
    } catch (e) {
      console.error("recipe load failed", e);
    }
  };

  return (
    <div className="text-xs flex flex-col h-full min-h-0">
      <div className="px-3 py-2 border-b border-border flex items-center gap-2">
        <span className="text-text-muted text-[10px] uppercase tracking-wider">
          ② Simulation
        </span>
      </div>

      {overrideCount > 0 && (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-accent/5">
          <span className="text-accent text-[11px] font-medium">
            {overrideCount} override{overrideCount === 1 ? "" : "s"}
          </span>
          <div className="ml-auto flex gap-2">
            <button
              onClick={onSaveAsNew}
              disabled={saving}
              className="text-[10px] text-text-secondary hover:text-text-primary disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save as new…"}
            </button>
            <button
              onClick={onResetAll}
              disabled={saving}
              className="text-[10px] text-warning hover:text-text-primary disabled:opacity-50"
            >
              Reset all
            </button>
          </div>
        </div>
      )}
      {strpError && (
        <div className="px-3 py-1 text-error text-[10px] bg-error/10 border-b border-error/30">
          {strpError}
        </div>
      )}

      <div className="px-3 py-2 flex items-center gap-2">
        <span className="text-text-muted text-[10px] uppercase tracking-wider">
          Recipe
        </span>
        <select
          value={activeRecipeName ?? ""}
          onChange={(e) => onPickRecipe(e.target.value)}
          className="flex-1 bg-elevated text-text-primary text-[11px] rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <option value="" disabled>
            Pick a recipe…
          </option>
          <optgroup label="Built-in">
            {(recipes as RecipeListItem[])
              .filter((r) => r.source === "builtin")
              .map((r) => (
                <option key={r.name} value={r.name}>{r.name}</option>
              ))}
          </optgroup>
          <optgroup label="User saved (★)">
            {(recipes as RecipeListItem[])
              .filter((r) => r.source === "user")
              .map((r) => (
                <option key={r.name} value={r.name}>★ {r.name}</option>
              ))}
          </optgroup>
        </select>
      </div>

      {activeRecipeName && (
        <div className="px-3 pb-2">
          <div className="flex bg-elevated rounded p-0.5">
            <button
              onClick={() => setViewPersist("form")}
              className={
                "flex-1 px-2 py-1 text-[10px] rounded " +
                (view === "form" ? "bg-accent/15 text-accent" : "text-text-muted")
              }
            >
              Form
            </button>
            <button
              onClick={() => setViewPersist("json")}
              className={
                "flex-1 px-2 py-1 text-[10px] rounded " +
                (view === "json" ? "bg-accent/15 text-accent" : "text-text-muted")
              }
            >
              JSON
            </button>
          </div>
        </div>
      )}

      {!activeRecipeName ? (
        <div className="px-3 py-4 text-xs text-text-muted text-center">
          Pick a recipe above to configure simulation.
        </div>
      ) : (
        <div
          className={
            "flex-1 min-h-0 flex flex-col " +
            (view === "form" ? "overflow-y-auto" : "overflow-hidden")
          }
        >
          {view === "form" ? <Properties /> : <SimJsonBody />}
        </div>
      )}

      {showFinishedToast && (
        <div className="mx-3 mb-2 px-3 py-2 bg-success/10 border border-success/30 text-success text-[11px] rounded flex items-center gap-2">
          <span>Run finished</span>
          <button
            onClick={() => {
              const lastSeq = useStore.getState().simRunName;
              if (lastSeq) {
                useStore.getState().resetForNewRun(lastSeq);
                useStore.getState().setSimState("done");
              }
              setShowFinishedToast(false);
            }}
            className="ml-auto text-success hover:underline"
          >
            View sequence
          </button>
          <button
            onClick={() => setShowFinishedToast(false)}
            className="text-text-muted hover:text-text-primary"
          >
            ✕
          </button>
        </div>
      )}

      <div className="px-3 py-2 border-t border-border flex items-center gap-2">
        <RunButton subscribe={subscribe} />
      </div>
    </div>
  );
}

/** JSON body: edits the effective config. Diffing back to overrides is
 *  handled by computing per-key diffs and dispatching setOverride or
 *  clearOverride. Run button is disabled while a parse error is active.
 *
 *  Note on "key removed from JSON": we treat it as a no-op rather than
 *  reverting to baseline. Aggressive interpretation would clear the
 *  override; conservative interpretation leaves it untouched. The
 *  conservative version is what's implemented: the user has to
 *  explicitly type the baseline value (or use the Form's ⤺ button) to
 *  revert. */
function SimJsonBody() {
  const baseline      = useStore((s) => s.simRecipeBaseline);
  const setRunBlocked = useStore((s) => s.setRunBlockedByJson);
  const { effective, setOverride, clearOverride } = useOverrides();

  const onChange = (parsed: Record<string, unknown>) => {
    if (!baseline) return;
    // Diff parsed against baseline. For every key in parsed:
    //   - different from baseline → setOverride
    //   - equal to baseline       → clearOverride
    // Keys missing from parsed are left alone (see SimJsonBody comment).
    const allKeys = new Set([
      ...Object.keys(baseline),
      ...Object.keys(parsed),
    ]);
    for (const k of allKeys) {
      const inParsed = Object.prototype.hasOwnProperty.call(parsed, k);
      if (!inParsed) continue;
      const a = JSON.stringify(parsed[k]);
      const b = JSON.stringify(baseline[k]);
      if (a !== b) setOverride(k, parsed[k]);
      else clearOverride(k);
    }
  };

  const onError = (msg: string | null) => setRunBlocked(!!msg);

  return (
    <JsonEditor
      value={effective}
      baseline={baseline}
      onChange={onChange}
      onError={onError}
    />
  );
}
