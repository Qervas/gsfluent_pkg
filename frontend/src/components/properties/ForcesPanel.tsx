import { useStore } from "@/lib/store";
import { Vec3Input } from "./widgets/Vec3Input";

export function ForcesPanel() {
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;
  const setField = (key: string, v: unknown) => setActiveRecipe(name, { ...data, [key]: v });

  const g = (data.g as number[] | undefined) ?? [0, 0, -15];
  const gTuple: [number, number, number] = [
    Number(g[0] ?? 0),
    Number(g[1] ?? 0),
    Number(g[2] ?? 0),
  ];

  return (
    <div className="space-y-1">
      <Vec3Input
        label="Gravity (x, y, z)"
        value={gTuple}
        onChange={(v) => setField("g", [v[0], v[1], v[2]])}
        step={0.5}
        hint="Gravity vector. Default (0, 0, -15) for z-up world."
      />
    </div>
  );
}
