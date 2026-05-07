import { useStore } from "@/lib/store";
import { SliderInput } from "./widgets/SliderInput";
import { NumberInput } from "./widgets/NumberInput";
import { SwitchInput } from "./widgets/SwitchInput";

const SLIDER_FIELDS: Array<[string, string, [number, number, number], string?]> = [
  ["init_azimuthm",   "Init azimuth (deg)",  [0, 360, 1]],
  ["init_elevation",  "Init elevation",      [-45, 60, 1]],
  ["init_radius",     "Init radius",         [1, 500, 1]],
];

const NUMBER_FIELDS: Array<[string, string, number, string?]> = [
  ["delta_a",  "Camera dA",  0.1],
  ["delta_e",  "Camera dE",  0.05],
  ["delta_r",  "Camera dR",  0.05],
];

export function CameraPanel() {
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;
  const setField = (key: string, v: unknown) => setActiveRecipe(name, { ...data, [key]: v });

  return (
    <div className="space-y-1">
      {SLIDER_FIELDS.map(([key, label, [min, max, step], hint]) => (
        <SliderInput
          key={key}
          label={label}
          value={Number(data[key] ?? 0)}
          onChange={(n) => setField(key, n)}
          min={min}
          max={max}
          step={step}
          hint={hint}
        />
      ))}
      {NUMBER_FIELDS.map(([key, label, step, hint]) => (
        <NumberInput
          key={key}
          label={label}
          value={Number(data[key] ?? 0)}
          onChange={(n) => setField(key, n)}
          step={step}
          hint={hint}
        />
      ))}
      <SwitchInput
        label="Move camera"
        value={Boolean(data.move_camera)}
        onChange={(v) => setField("move_camera", v)}
        hint="Auto-orbit camera per dA/dE/dR each frame."
      />
      <NumberInput
        label="Default cam idx"
        value={Number(data.default_camera_index ?? -1)}
        onChange={(n) => setField("default_camera_index", n)}
        step={1}
      />
    </div>
  );
}
