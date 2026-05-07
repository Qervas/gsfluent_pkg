import { PropertyFolder } from "./PropertyFolder";
import { useStore } from "@/lib/store";
import { MaterialPanel } from "./MaterialPanel";
import { SolverPanel } from "./SolverPanel";
import { ForcesPanel } from "./ForcesPanel";
import { SimSetupPanel } from "./SimSetupPanel";
import { CameraPanel } from "./CameraPanel";
import { ParticleFillingPanel } from "./ParticleFillingPanel";
import { OtherPanel } from "./OtherPanel";

const PROVENANCE_KEY = "_provenance";

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
      <PropertyFolder title="Material">
        <MaterialPanel />
      </PropertyFolder>
      <PropertyFolder title="Solver" defaultOpen={false}>
        <SolverPanel />
      </PropertyFolder>
      <PropertyFolder title="Forces" defaultOpen={false}>
        <ForcesPanel />
      </PropertyFolder>
      <PropertyFolder title="Sim setup" defaultOpen={false}>
        <SimSetupPanel />
      </PropertyFolder>
      <PropertyFolder title="Camera" defaultOpen={false}>
        <CameraPanel />
      </PropertyFolder>
      <PropertyFolder title="Particle filling" defaultOpen={false}>
        <ParticleFillingPanel />
      </PropertyFolder>
      <PropertyFolder title="Other" defaultOpen={false}>
        <OtherPanel />
      </PropertyFolder>
      <PropertyFolder title="Boundary conditions" defaultOpen={false}>{/* Phase 4.3 */}</PropertyFolder>
      <PropertyFolder title="Provenance" defaultOpen={false}>
        <ProvenanceFooter data={activeRecipeData} />
      </PropertyFolder>
    </div>
  );
}

function ProvenanceFooter({ data }: { data: Record<string, unknown> }) {
  const p = data[PROVENANCE_KEY] as
    | { based_on?: string; saved_at?: string }
    | undefined;
  if (!p) {
    return (
      <div className="text-text-secondary py-1">Built-in preset.</div>
    );
  }
  return (
    <div className="text-text-secondary py-1 space-y-0.5">
      <div>
        Based on <span className="text-accent">{p.based_on ?? "(unknown)"}</span>
      </div>
      {p.saved_at && (
        <div className="text-text-muted">saved {p.saved_at}</div>
      )}
    </div>
  );
}
