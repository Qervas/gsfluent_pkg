import { PropertyFolder } from "./PropertyFolder";
import { useStore } from "@/lib/store";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ComposerPanel } from "./ComposerPanel";
import { MaterialPanel } from "./MaterialPanel";
import { SolverPanel } from "./SolverPanel";
import { ForcesPanel } from "./ForcesPanel";
import { SimSetupPanel } from "./SimSetupPanel";
import { ParticleFillingPanel } from "./ParticleFillingPanel";
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

  const hasRecipe = !!activeRecipeName && !!activeRecipeData;

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

        {/* PRIMARY authoring surface: compose material x scenario x building.
            Always shown — it's also the entry point when no recipe is active
            yet (it composes a verified default into the active recipe). */}
        <PropertyFolder title="Composer"><ComposerPanel /></PropertyFolder>

        {/* Everything below is ADVANCED override tooling on top of the
            composed recipe — collapsed by default, and only meaningful once a
            recipe is active. Camera + Other panels were removed: they edited
            preview/viser-only fields the in-browser playback never reads (the
            recipe still carries them, set by the composer's camera block). */}
        {hasRecipe && (
          <>
            <PropertyFolder title="Material fine-tune" defaultOpen={false}><MaterialPanel /></PropertyFolder>
            <PropertyFolder title="Solver" defaultOpen={false}><SolverPanel /></PropertyFolder>
            <PropertyFolder title="Forces" defaultOpen={false}><ForcesPanel /></PropertyFolder>
            <PropertyFolder title="Sim setup" defaultOpen={false}><SimSetupPanel /></PropertyFolder>
            <PropertyFolder title="Particle filling" defaultOpen={false}><ParticleFillingPanel /></PropertyFolder>
            <PropertyFolder title="Boundary conditions" defaultOpen={false}><BoundaryEditor /></PropertyFolder>
            <PropertyFolder title="Provenance" defaultOpen={false}>
              <ProvenanceFooter />
            </PropertyFolder>
          </>
        )}
      </div>
    </TooltipProvider>
  );
}
