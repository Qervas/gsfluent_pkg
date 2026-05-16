import { usePanelData } from "@/lib/use-overrides";
import { NumberInput } from "./widgets/NumberInput";

// Hints for the known particle_filling sub-keys. The block is an opaque
// dict in the upstream sim, so we render whatever keys appear; this map
// just labels the ones we recognize. Unknown keys get rendered without
// a tooltip.
const HINTS: Record<string, string> = {
  n_grid:                  "Voxel resolution used by the filler. Independent of solver n_grid — finer = more particles for the same volume.",
  max_particles_num:       "Hard cap on particles inserted. Filler stops once this is reached.",
  density_threshold:       "Voxel density threshold below which a cell is treated as empty (no particles inserted).",
  search_threshold:        "Ray-cast hit threshold used to decide whether a voxel is inside or outside the surface.",
  max_partciels_per_cell:  "Max particles emitted per voxel cell. Caps local density. (Note: upstream typo; key spelling matches the sim.)",
  search_exclude_direction: "Skip this ray-cast direction during the inside/outside test (0–5 → ±X, ±Y, ±Z).",
  ray_cast_direction:       "Primary ray direction for the inside/outside test (0–5 → ±X, ±Y, ±Z).",
};

export function ParticleFillingPanel() {
  const panel = usePanelData();
  if (!panel) return null;
  const { data, setField } = panel;

  const pf = (data.particle_filling as Record<string, unknown> | undefined) ?? {};
  const keys = Object.keys(pf);

  if (keys.length === 0) {
    return (
      <div className="text-text-muted text-xs py-1">
        Recipe has no `particle_filling` block.
      </div>
    );
  }

  // particle_filling is a nested object; override the whole block as a
  // single key so the engine's top-level merge stays simple.
  const setChild = (key: string, v: number) => {
    setField("particle_filling", { ...pf, [key]: v });
  };

  return (
    <div className="space-y-1">
      {keys.map((k) => {
        const v = pf[k];
        if (typeof v !== "number") return null; // skip non-scalar children for MVP
        return (
          <NumberInput
            key={k}
            label={k}
            value={v}
            onChange={(n) => setChild(k, n)}
            step={1}
            hint={HINTS[k]}
          />
        );
      })}
    </div>
  );
}
