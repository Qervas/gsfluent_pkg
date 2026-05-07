import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { SliderInput } from "./widgets/SliderInput";
import { SelectInput } from "./widgets/SelectInput";

const MATERIALS = [
  "jelly", "metal", "sand", "foam", "snow", "plasticine", "watermelon",
];

// (key, label, [min, max, step], hint?)
const MATERIAL_FIELDS: Array<[string, string, [number, number, number], string?]> = [
  ["E",              "Young's E",       [100, 1e7, 100],   "Stiffness — higher = harder material."],
  ["nu",             "Poisson ν",       [0, 0.499, 0.005], "Lateral contraction."],
  ["density",        "Density",         [0.01, 100, 0.01], "Mass per unit volume."],
  ["yield_stress",   "Yield stress",    [0, 1e6, 1],       "Stress threshold for plastic flow."],
  ["friction_angle", "Friction (deg)",  [0, 90, 1],        "Drucker-Prager friction angle."],
];

export function MaterialPanel() {
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  const { data: defaults } = useQuery({
    queryKey: ["material_defaults"],
    queryFn: api.schemas.materials,
  });

  if (!activeRecipeData || !activeRecipeName) return null;

  const setField = (key: string, v: unknown) => {
    setActiveRecipe(activeRecipeName, { ...activeRecipeData, [key]: v });
  };

  const onMaterialChange = (newMat: string) => {
    if (!defaults) return;
    const mDefaults = defaults[newMat] ?? {};
    setActiveRecipe(activeRecipeName, {
      ...activeRecipeData,
      material: newMat,
      ...mDefaults,
    });
  };

  const currentMat = (activeRecipeData.material as string | undefined) ?? "jelly";

  return (
    <div className="space-y-1">
      <SelectInput
        label="Material"
        value={currentMat}
        options={MATERIALS}
        onChange={onMaterialChange}
        hint="Picking a material snaps related params (E, ν, density, ...) to validated defaults."
      />
      {MATERIAL_FIELDS.map(([key, label, [min, max, step], hint]) => {
        const v = Number(activeRecipeData[key] ?? 0);
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
    </div>
  );
}
