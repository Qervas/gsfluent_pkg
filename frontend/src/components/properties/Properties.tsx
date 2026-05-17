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
import { useQuery } from "@tanstack/react-query";
import type { RecipeListItem } from "@/lib/types";

export function Properties() {
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const { overrideCount } = useOverrides();

  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });
  const baselineExists = activeRecipeName
    ? (recipes as RecipeListItem[]).some((r) => r.name === activeRecipeName)
    : true;

  if (!activeRecipeName || !activeRecipeData) {
    return (
      <div className="p-3 text-xs text-text-muted">
        Select a recipe in the Outliner to edit parameters.
      </div>
    );
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="text-xs">
        {!baselineExists && activeRecipeName && (
          <div className="px-3 py-2 border-b border-warning bg-warning/10 text-warning text-[11px]">
            Baseline <span className="font-mono">{activeRecipeName}</span> was
            deleted. Your {overrideCount} edits are now standalone — save them
            as a new recipe.
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
