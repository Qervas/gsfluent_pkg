import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { SliderInput } from "./widgets/SliderInput";
import { NumberInput } from "./widgets/NumberInput";

const FIELDS: Array<[string, string, [number, number, number], string?]> = [
  ["n_grid",                 "Grid resolution",         [50, 400, 10],       "MPM grid cells per side. Cubic memory cost; doubling triples runtime."],
  ["grid_lim",               "Grid lim",                [1, 10, 1],          "Half-width of the sim cube in MPM-normalized units. Grid spans [0, 2·grid_lim] per axis."],
  ["frame_dt",               "Frame dt (s)",            [0.005, 0.1, 0.005], "Wall-clock interval each frame represents. Frame count × frame_dt = total sim time."],
  ["frame_num",              "Total frames",            [30, 600, 10],       "Number of frames to simulate. Output: simulation_ply/sim_0000.ply … sim_{N-1}.ply."],
  ["flip_pic_ratio",         "FLIP/PIC ratio",          [0, 1, 0.05],        "0 = pure PIC (more damped, stable); 1 = pure FLIP (livelier motion). 0.7 is a good default for jelly."],
  ["rpic_damping",           "RPIC damping",            [0, 1, 0.01],        "Rotational PIC damping. 0 = none; higher kills angular motion."],
  ["grid_v_damping_scale",   "Grid v damping scale",    [0.5, 2, 0.05],      "Multiplier on grid-velocity damping. >1 dampens harder each step; <1 lets motion persist longer."],
];

export function SolverPanel() {
  const { effective, setOverride } = useOverrides();
  const name = useStore((s) => s.activeRecipeName);
  if (!name || !effective) return null;
  const setField = (key: string, v: unknown) => setOverride(key, v);
  // Local alias so the remaining `data.<key>` reads keep working.
  const data = effective;

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
        hint="Inner integration timestep. Smaller = more stable but slower (more substeps per frame). 1e-4 is typical."
      />
    </div>
  );
}
