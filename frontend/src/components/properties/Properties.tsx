import { PropertyFolder } from "./PropertyFolder";
import { useStore } from "@/lib/store";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MaterialPanel } from "./MaterialPanel";
import { SolverPanel } from "./SolverPanel";
import { ForcesPanel } from "./ForcesPanel";
import { SimSetupPanel } from "./SimSetupPanel";
import { CameraPanel } from "./CameraPanel";
import { ParticleFillingPanel } from "./ParticleFillingPanel";
import { OtherPanel } from "./OtherPanel";
import { BoundaryEditor } from "./BoundaryEditor";
import { ProvenanceFooter } from "./ProvenanceFooter";
import { useOverrides } from "@/lib/use-overrides";
import { api } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

export function Properties() {
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const { effective, overrideCount, clearAllOverrides } = useOverrides();
  const qc = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!activeRecipeName || !activeRecipeData) {
    return (
      <div className="p-3 text-xs text-text-muted">
        Select a recipe in the Outliner to edit parameters.
      </div>
    );
  }

  const onSaveAsNew = async () => {
    const name = prompt("Save as new recipe — name:");
    if (!name?.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.recipes.save(name.trim(), effective, activeRecipeName);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      // Switch the active recipe to the new one. The new recipe IS
      // the effective config, so overrides clear naturally.
      useStore.getState().loadActiveRecipe(name.trim(), effective);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onResetAll = () => {
    if (overrideCount >= 3) {
      if (!confirm(`Reset ${overrideCount} overrides?`)) return;
    }
    clearAllOverrides();
  };

  return (
    <TooltipProvider delayDuration={150}>
      <div className="text-xs">
        {/* Override status strip — surfaces deviation count + bulk actions */}
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
                {saving ? "Saving…" : "Save as new recipe…"}
              </button>
              <button
                onClick={onResetAll}
                className="text-[10px] text-warning hover:text-text-primary"
              >
                Reset all
              </button>
            </div>
          </div>
        )}
        {error && (
          <div className="px-3 py-1 text-error text-[10px] bg-error/10 border-b border-error/30">
            {error}
          </div>
        )}

        <PropertyFolder title="Material"><MaterialPanel /></PropertyFolder>
        <PropertyFolder title="Solver" defaultOpen={false}><SolverPanel /></PropertyFolder>
        <PropertyFolder title="Forces" defaultOpen={false}><ForcesPanel /></PropertyFolder>
        <PropertyFolder title="Sim setup" defaultOpen={false}><SimSetupPanel /></PropertyFolder>
        <PropertyFolder title="Camera" defaultOpen={false}><CameraPanel /></PropertyFolder>
        <PropertyFolder title="Particle filling" defaultOpen={false}><ParticleFillingPanel /></PropertyFolder>
        <PropertyFolder title="Other" defaultOpen={false}><OtherPanel /></PropertyFolder>
        <PropertyFolder title="Boundary conditions" defaultOpen={false}><BoundaryEditor /></PropertyFolder>
        <PropertyFolder title="Provenance" defaultOpen={false}>
          <ProvenanceFooter />
        </PropertyFolder>
      </div>
    </TooltipProvider>
  );
}
