import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { SliderInput } from "./widgets/SliderInput";
import { SwitchInput } from "./widgets/SwitchInput";

export function OtherPanel() {
  const { effective, setOverride } = useOverrides();
  const name = useStore((s) => s.activeRecipeName);
  if (!name || !effective) return null;
  const setField = (key: string, v: unknown) => setOverride(key, v);
  // Local alias so the remaining `data.<key>` reads keep working.
  const data = effective;

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
        hint="Upstream sim flag — overlay hint geometry (sim bounds, viewpoint center) in the preview render. Has no effect on the workbench viewport."
      />
    </div>
  );
}
