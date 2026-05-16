import { usePanelData } from "@/lib/use-overrides";
import { SliderInput } from "./widgets/SliderInput";
import { SwitchInput } from "./widgets/SwitchInput";

export function OtherPanel() {
  const panel = usePanelData();
  if (!panel) return null;
  const { data, setField } = panel;

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
