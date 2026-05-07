import { useStore } from "@/lib/store";
import { NumberInput } from "./widgets/NumberInput";

export function ParticleFillingPanel() {
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;

  const pf = (data.particle_filling as Record<string, unknown> | undefined) ?? {};
  const keys = Object.keys(pf);

  if (keys.length === 0) {
    return (
      <div className="text-text-muted text-xs py-1">
        Recipe has no `particle_filling` block.
      </div>
    );
  }

  const setChild = (key: string, v: number) => {
    setActiveRecipe(name, { ...data, particle_filling: { ...pf, [key]: v } });
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
          />
        );
      })}
    </div>
  );
}
