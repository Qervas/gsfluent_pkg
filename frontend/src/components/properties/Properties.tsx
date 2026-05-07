import { PropertyFolder } from "./PropertyFolder";
import { useStore } from "@/lib/store";
import { MaterialPanel } from "./MaterialPanel";
import { SolverPanel } from "./SolverPanel";
import { ForcesPanel } from "./ForcesPanel";
import { SimSetupPanel } from "./SimSetupPanel";
import { CameraPanel } from "./CameraPanel";
import { ParticleFillingPanel } from "./ParticleFillingPanel";
import { OtherPanel } from "./OtherPanel";
import { BoundaryEditor } from "./BoundaryEditor";
import { ProvenanceFooter } from "./ProvenanceFooter";
import { SavePresetDialog } from "./SavePresetDialog";

export function Properties() {
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);

  if (!activeRecipeName || !activeRecipeData) {
    return (
      <div className="p-3 text-xs text-text-muted">
        Select a recipe in the Outliner to edit parameters.
      </div>
    );
  }

  return (
    <div className="text-xs">
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
      <div className="px-3 pt-3 pb-2 border-t border-border">
        <SavePresetDialog />
      </div>
    </div>
  );
}
