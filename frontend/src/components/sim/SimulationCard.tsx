import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { Properties } from "@/components/properties/Properties";
import { JsonEditor } from "@/components/properties/widgets/JsonEditor";
import { RunButton } from "@/components/runs/RunButton";
import type { RecipeListItem } from "@/lib/types";

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
  const loadActiveRecipe  = useStore((s) => s.loadActiveRecipe);
  const { overrideCount } = useOverrides();
  const [view, setView]   = useState<"form" | "json">(
    () => (localStorage.getItem("gsfluent.sim_view_mode") as "form" | "json") || "form",
  );

  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });

  const setViewPersist = (v: "form" | "json") => {
    setView(v);
    localStorage.setItem("gsfluent.sim_view_mode", v);
  };

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
        {overrideCount > 0 && (
          <span className="text-[10px] text-accent px-1.5 py-0.5 bg-accent/10 rounded">
            {overrideCount} override{overrideCount === 1 ? "" : "s"}
          </span>
        )}
      </div>

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
        <div className="flex-1 min-h-0 overflow-y-auto">
          {view === "form" ? <Properties /> : <SimJsonBody />}
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
