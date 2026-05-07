import { useStore } from "@/lib/store";
import { SliderInput } from "./widgets/SliderInput";
import { SwitchInput } from "./widgets/SwitchInput";

export function OtherPanel() {
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  if (!data || !name) return null;
  const setField = (key: string, v: unknown) => setActiveRecipe(name, { ...data, [key]: v });

  return (
    <div className="space-y-1">
      <SliderInput
        label="Opacity threshold"
        value={Number(data.opacity_threshold ?? 0)}
        onChange={(n) => setField("opacity_threshold", n)}
        min={0}
        max={1}
        step={0.05}
        hint="Splats below this opacity are rendered transparent."
      />
      <SwitchInput
        label="Show hint"
        value={Boolean(data.show_hint)}
        onChange={(v) => setField("show_hint", v)}
      />
    </div>
  );
}
