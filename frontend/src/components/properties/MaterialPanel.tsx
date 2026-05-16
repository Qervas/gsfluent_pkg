import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { ScientificInput, type Marker } from "./widgets/ScientificInput";
import { SelectInput } from "./widgets/SelectInput";

/** Material model + the parameters each one actually consumes.
 *
 * Why gating: jelly is pure neo-Hookean elasticity — friction_angle is
 * literally ignored by the solver. Showing it anyway invites users to
 * "tune" a value that has zero effect, which is the opposite of
 * scientific. Sand is the inverse: Drucker-Prager driven entirely by
 * `friction_angle`; its `E` is set high enough to keep cohesion stable
 * but the user shouldn't be sweeping it.
 *
 * The boolean per field decides visibility in this panel only — every
 * material still carries every field in the recipe JSON (the solver
 * reads what it needs and silently ignores the rest). Gating is purely
 * a UX layer.
 */
type FieldKey = "E" | "nu" | "density" | "yield_stress" | "friction_angle";

const MATERIAL_FIELDS: Record<string, Record<FieldKey, boolean>> = {
  jelly:       { E: true, nu: true, density: true, yield_stress: false, friction_angle: false },
  metal:       { E: true, nu: true, density: true, yield_stress: true,  friction_angle: false },
  plasticine:  { E: true, nu: true, density: true, yield_stress: true,  friction_angle: false },
  foam:        { E: true, nu: true, density: true, yield_stress: false, friction_angle: false },
  snow:        { E: true, nu: true, density: true, yield_stress: true,  friction_angle: false },
  sand:        { E: true, nu: true, density: true, yield_stress: false, friction_angle: true  },
  watermelon:  { E: true, nu: true, density: true, yield_stress: true,  friction_angle: false },
};

const MATERIALS = Object.keys(MATERIAL_FIELDS);

/** Per-parameter spec. Reference markers are calibrated against the
 *  values used by the bundled recipes — so the user can see "where
 *  does the snow recipe sit on the stiffness axis vs the metal one." */
type FieldSpec = {
  label: string;
  unit?: string;
  scale: "linear" | "log";
  min: number;
  max: number;
  step: number;
  hint: string;
  markers?: Marker[];
};

const FIELD_SPECS: Record<FieldKey, FieldSpec> = {
  E: {
    label: "Young's E",
    unit: "sim",
    scale: "log",
    min: 10,
    max: 1e7,
    step: 1,
    hint:
      "Young's modulus — material stiffness. Logarithmic axis because " +
      "the meaningful range spans 5+ orders of magnitude. Sim-internal " +
      "units (proportional to Pa under the recipe's mass/length scaling).",
    markers: [
      { value: 50,    label: "soft foam" },
      { value: 500,   label: "jelly" },
      { value: 5000,  label: "firm" },
      { value: 50000, label: "metal" },
    ],
  },
  nu: {
    label: "Poisson ν",
    scale: "linear",
    min: 0,
    max: 0.499,
    step: 0.005,
    hint:
      "Poisson ratio (0 ≤ ν < 0.5). Higher = less lateral compression. " +
      "ν → 0.5 is incompressible (rubber, jelly); ν ≈ 0.3 is typical " +
      "for metals; ν ≈ 0.1 is granular.",
    markers: [
      { value: 0.1,  label: "granular" },
      { value: 0.3,  label: "metal" },
      { value: 0.49, label: "rubber" },
    ],
  },
  density: {
    label: "Density",
    unit: "sim",
    scale: "linear",
    min: 0.1,
    max: 10,
    step: 0.05,
    hint:
      "Mass per particle (sim units). Sets the gravity / momentum " +
      "response. Reference: water = 1 in the bundled recipes.",
    markers: [
      { value: 0.3, label: "foam" },
      { value: 1,   label: "water" },
      { value: 3,   label: "metal" },
    ],
  },
  yield_stress: {
    label: "Yield stress",
    unit: "sim",
    scale: "log",
    min: 1,
    max: 1e6,
    step: 1,
    hint:
      "Stress threshold at which the material starts flowing plastically. " +
      "Below: elastic recoil. Above: permanent deformation. Logarithmic " +
      "axis because the useful range spans many orders of magnitude.",
    markers: [
      { value: 100,    label: "soft" },
      { value: 500,    label: "plastic" },
      { value: 5_000,  label: "metal" },
    ],
  },
  friction_angle: {
    label: "Friction",
    unit: "°",
    scale: "linear",
    min: 0,
    max: 60,
    step: 1,
    hint:
      "Drucker-Prager internal friction angle. Higher = more grainy / " +
      "load-bearing. Dry sand ≈ 30–35°; wet sand ≈ 25°; gravel ≈ 40°.",
    markers: [
      { value: 25, label: "wet" },
      { value: 35, label: "dry" },
      { value: 45, label: "gravel" },
    ],
  },
};

/** Render order — keeps Young's E + Poisson together (they're the
 *  Hookean pair), then density (which the rest are stacked on), then
 *  the plasticity / friction params last so users scanning top-down
 *  see the most-tuned knobs first. */
const FIELD_ORDER: FieldKey[] = ["E", "nu", "density", "yield_stress", "friction_angle"];

export function MaterialPanel() {
  const { effective, baselineValue, setOverride, clearOverride } = useOverrides();
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const { data: defaults } = useQuery({
    queryKey: ["material_defaults"],
    queryFn: api.schemas.materials,
  });

  if (!activeRecipeName || !effective) return null;

  const setField = (key: string, v: unknown) => setOverride(key, v);

  const onMaterialChange = (newMat: string) => {
    if (!defaults) return;
    const mDefaults = defaults[newMat] ?? {};
    // Material switch is a baseline edit, not an override. Update both
    // activeRecipeData (so other panels read the new defaults) and the
    // store's baseline, then clear overrides — none of the previous
    // overrides necessarily apply to the new material.
    const next = { ...effective, material: newMat, ...mDefaults };
    useStore.getState().setActiveRecipe(activeRecipeName, next);
    useStore.getState().setSimRecipeBaseline(next);
    useStore.getState().clearAllOverrides();
  };

  const currentMat = (effective.material as string | undefined) ?? "jelly";
  const visibility = MATERIAL_FIELDS[currentMat] ?? MATERIAL_FIELDS.jelly;

  return (
    <div className="space-y-0.5">
      <SelectInput
        label="Material"
        value={currentMat}
        options={MATERIALS}
        onChange={onMaterialChange}
        hint="Picking a material snaps related params (E, ν, density, …) to validated defaults and hides parameters the model ignores."
      />
      {FIELD_ORDER.filter((k) => visibility[k]).map((key) => {
        const spec = FIELD_SPECS[key];
        const v = Number(effective[key] ?? 0);
        const b = Number(baselineValue(key) ?? NaN);
        return (
          <ScientificInput
            key={key}
            label={spec.label}
            value={v}
            baselineValue={Number.isFinite(b) ? b : undefined}
            onChange={(n) => setField(key, n)}
            onRevert={() => clearOverride(key)}
            min={spec.min}
            max={spec.max}
            step={spec.step}
            unit={spec.unit}
            scale={spec.scale}
            hint={spec.hint}
            markers={spec.markers}
          />
        );
      })}
      {FIELD_ORDER.some((k) => !visibility[k]) && (
        <div className="px-1 pt-1 text-[10px] text-text-muted italic leading-tight">
          {hiddenList(visibility)} hidden — not used by{" "}
          <span className="font-mono">{currentMat}</span>.
        </div>
      )}
    </div>
  );
}

function hiddenList(v: Record<FieldKey, boolean>): string {
  const names: Record<FieldKey, string> = {
    E: "Young's E",
    nu: "Poisson ν",
    density: "Density",
    yield_stress: "Yield stress",
    friction_angle: "Friction",
  };
  const hidden = FIELD_ORDER.filter((k) => !v[k]).map((k) => names[k]);
  if (hidden.length === 1) return hidden[0];
  if (hidden.length === 2) return hidden.join(" + ");
  return hidden.slice(0, -1).join(", ") + " + " + hidden.at(-1);
}
