import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { SliderInput } from "./widgets/SliderInput";
import { NumberInput } from "./widgets/NumberInput";
import { SwitchInput } from "./widgets/SwitchInput";

// NOTE: all Camera params drive the upstream preview-rasterizer
// (gs_simulation_building.py renders per-frame RGB pngs using these).
// The workbench plays back simulation_ply/*.ply via R3F / viser with
// its own interactive camera, so these fields are sim-side preview
// only — they don't affect anything you see in the viewport.
const SLIDER_FIELDS: Array<[string, string, [number, number, number], string?]> = [
  ["init_azimuthm",   "Init azimuth (deg)",  [0, 360, 1],   "Starting camera azimuth around viewpoint center, degrees. Preview-only."],
  ["init_elevation",  "Init elevation",      [-45, 60, 1],  "Starting camera pitch above horizontal, degrees. Preview-only."],
  ["init_radius",     "Init radius",         [1, 500, 1],   "Starting camera distance from viewpoint center, world units. Preview-only."],
];

const NUMBER_FIELDS: Array<[string, string, number, string?]> = [
  ["delta_a",  "Camera dA",  0.1,   "Azimuth delta per frame (deg). Only applied when Move camera is on."],
  ["delta_e",  "Camera dE",  0.05,  "Elevation delta per frame (deg). Only applied when Move camera is on."],
  ["delta_r",  "Camera dR",  0.05,  "Radius delta per frame (world units). Only applied when Move camera is on."],
];

export function CameraPanel() {
  const { effective, setOverride } = useOverrides();
  const name = useStore((s) => s.activeRecipeName);
  if (!name || !effective) return null;
  const setField = (key: string, v: unknown) => setOverride(key, v);
  // Local alias so the remaining `data.<key>` reads keep working.
  const data = effective;

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
        hint="Auto-orbit the preview camera per dA/dE/dR each frame. Preview-only — the workbench viewport is always interactive."
      />
      <NumberInput
        label="Default cam idx"
        value={Number(data.default_camera_index ?? -1)}
        onChange={(n) => setField("default_camera_index", n)}
        step={1}
        hint="Index into the model's cameras.json for the preview camera. -1 = synthesize from init_azimuthm/elevation/radius."
      />
    </div>
  );
}
