import { usePanelData } from "@/lib/use-overrides";
import { SliderInput } from "./widgets/SliderInput";
import { NumberInput } from "./widgets/NumberInput";

const FIELDS: Array<[string, string, [number, number, number], string?]> = [
  ["n_grid",                 "Grid resolution",         [50, 400, 10],       "MPM grid cells per side. Cubic memory cost; doubling triples runtime."],
  ["grid_lim",               "Grid lim",                [1, 10, 1],          "Half-width of the sim cube in MPM-normalized units. Grid spans [0, 2·grid_lim] per axis."],
  ["frame_dt",               "Frame dt (s)",            [0.005, 0.1, 0.005], "Wall-clock interval each frame represents. Frame count × frame_dt = total sim time."],
  ["flip_pic_ratio",         "FLIP/PIC ratio",          [0, 1, 0.05],        "0 = pure PIC (more damped, stable); 1 = pure FLIP (livelier motion). 0.7 is a good default for jelly."],
  ["rpic_damping",           "RPIC damping",            [0, 1, 0.01],        "Rotational PIC damping. 0 = none; higher kills angular motion."],
  ["grid_v_damping_scale",   "Grid v damping scale",    [0.5, 2, 0.05],      "Multiplier on grid-velocity damping. >1 dampens harder each step; <1 lets motion persist longer."],
];

export function SolverPanel() {
  const panel = usePanelData();
  if (!panel) return null;
  const { data, setField } = panel;

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
      {/* frame_num and substep_dt are number inputs (no slider cap).
          frame_num: a slider's range is too constraining — short test runs
          want 4-30 frames, longer animations want 1500+. Free-form typing
          covers both with no artificial ceiling.
          substep_dt: too small a magnitude for a useful slider. */}
      <NumberInput
        label="Total frames"
        value={Number(data.frame_num ?? 150)}
        onChange={(n) => setField("frame_num", Math.max(1, Math.round(n)))}
        step={1}
        hint="Any positive integer. 150 ≈ 5 s at 30 fps. Output: simulation_ply/sim_0000.ply … sim_{N-1}.ply. ~1.5 s/frame at 200k particles."
      />
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
