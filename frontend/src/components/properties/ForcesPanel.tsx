import { usePanelData } from "@/lib/use-overrides";
import { Vec3Input } from "./widgets/Vec3Input";

export function ForcesPanel() {
  const panel = usePanelData();
  if (!panel) return null;
  const { data, setField } = panel;

  const g = (data.g as number[] | undefined) ?? [0, 0, -15];
  const gTuple: [number, number, number] = [
    Number(g[0] ?? 0),
    Number(g[1] ?? 0),
    Number(g[2] ?? 0),
  ];

  return (
    <div className="space-y-1">
      <Vec3Input
        label="Gravity (x, y, z)"
        value={gTuple}
        onChange={(v) => setField("g", [v[0], v[1], v[2]])}
        step={0.5}
        hint="Gravitational acceleration vector (world units / s²). Default (0, 0, -15) is 1.5× Earth gravity for snappier motion on small scenes."
      />
    </div>
  );
}
