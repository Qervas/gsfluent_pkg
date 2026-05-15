import { useStore } from "@/lib/store";
import { Vec3Input } from "./widgets/Vec3Input";
import { NumberInput } from "./widgets/NumberInput";

export function SimSetupPanel() {
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;
  const setField = (key: string, v: unknown) => setActiveRecipe(name, { ...data, [key]: v });

  const center = (data.mpm_space_viewpoint_center as number[] | undefined) ?? [1, 1, 1];
  const upAxis = (data.mpm_space_vertical_upward_axis as number[] | undefined) ?? [0, 0, 1];
  const simArea = (data.sim_area as number[] | undefined) ?? [0, 0, 0, 0, 0, 0];
  const tuple = (a: number[]): [number, number, number] => [
    Number(a[0] ?? 0),
    Number(a[1] ?? 0),
    Number(a[2] ?? 0),
  ];

  const setSimAreaIdx = (i: number, n: number) => {
    const next = [...simArea];
    next[i] = n;
    setField("sim_area", next);
  };

  const axes = ["X", "Y", "Z"] as const;

  return (
    <div className="space-y-2">
      <div
        title="Axis-aligned bounding box of the sub-region simulated, in world coords. Only splats inside this box become MPM particles; everything else is treated as static background."
      >
        <div className="text-text-secondary text-xs mb-0.5">Sim bounds (world)</div>
        {axes.map((axis, i) => (
          <div key={axis} className="flex items-center gap-1 py-0.5">
            <span className="text-text-muted text-xs w-3">{axis}</span>
            <NumberInput
              label="min"
              value={Number(simArea[i * 2] ?? 0)}
              onChange={(n) => setSimAreaIdx(i * 2, n)}
              step={0.5}
              hint={`${axis}-axis lower bound (world units).`}
            />
            <NumberInput
              label="max"
              value={Number(simArea[i * 2 + 1] ?? 0)}
              onChange={(n) => setSimAreaIdx(i * 2 + 1, n)}
              step={0.5}
              hint={`${axis}-axis upper bound (world units).`}
            />
          </div>
        ))}
      </div>
      <Vec3Input
        label="Viewpoint center"
        value={tuple(center)}
        onChange={(v) => setField("mpm_space_viewpoint_center", [v[0], v[1], v[2]])}
        step={0.1}
        hint="World-space point that maps to the center of the MPM-normalized cube (typically (1,1,1) when grid_lim=2)."
      />
      <Vec3Input
        label="Up axis"
        value={tuple(upAxis)}
        onChange={(v) => setField("mpm_space_vertical_upward_axis", [v[0], v[1], v[2]])}
        step={1}
        hint="Unit vector for gravity's opposite direction. Use (0, 0, 1) for z-up (our default); (0, 1, 0) for legacy y-up sources."
      />
    </div>
  );
}
