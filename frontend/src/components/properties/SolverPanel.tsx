import { useStore } from "@/lib/store";
import { SliderInput } from "./widgets/SliderInput";
import { NumberInput } from "./widgets/NumberInput";

const FIELDS: Array<[string, string, [number, number, number], string?]> = [
  ["n_grid",                 "Grid resolution",         [50, 400, 10],     "MPM grid cells per side. Quadratic memory cost."],
  ["grid_lim",               "Grid lim",                [1, 10, 1],        "Grid extent in world units."],
  ["frame_dt",               "Frame dt (s)",            [0.005, 0.1, 0.005], "Time per frame."],
  ["frame_num",              "Total frames",            [30, 600, 10],     "Animation length."],
  ["flip_pic_ratio",         "FLIP/PIC ratio",          [0, 1, 0.05],      "0 = pure PIC, 1 = pure FLIP."],
  ["rpic_damping",           "RPIC damping",            [0, 1, 0.01],      "Velocity damping factor."],
  ["grid_v_damping_scale",   "Grid v damping scale",    [0.5, 2, 0.05]],
];

export function SolverPanel() {
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;
  const setField = (key: string, v: unknown) => setActiveRecipe(name, { ...data, [key]: v });

  return (
    <div className="space-y-1">
      {FIELDS.map(([key, label, [min, max, step], hint]) => {
        const v = Number(data[key] ?? 0);
        return (
          <SliderInput
            key={key}
            label={label}
            value={v}
            onChange={(n) => setField(key, n)}
            min={min}
            max={max}
            step={step}
            hint={hint}
          />
        );
      })}
      {/* substep_dt is too small for a slider — number input is more useful. */}
      <NumberInput
        label="Substep dt (s)"
        value={Number(data.substep_dt ?? 0.0001)}
        onChange={(n) => setField("substep_dt", n)}
        step={0.00001}
        hint="Inner integration step. Smaller = more stable."
      />
    </div>
  );
}
