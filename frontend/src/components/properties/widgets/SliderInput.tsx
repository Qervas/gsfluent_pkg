import { Slider } from "@/components/ui/slider";
import { HelpIcon } from "./HelpIcon";

export function SliderInput({
  label,
  value,
  onChange,
  min,
  max,
  step,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="text-text-secondary text-xs flex-1 truncate flex items-center gap-1">
        <span className="truncate">{label}</span>
        <HelpIcon hint={hint} />
      </span>
      <Slider
        className="w-20"
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => onChange(v[0])}
      />
      <input
        type="number"
        value={value}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (!Number.isNaN(n)) onChange(n);
        }}
        min={min}
        max={max}
        step={step}
        className="font-mono text-text-primary bg-elevated rounded px-1 w-16 text-right text-xs focus:outline-none focus:ring-1 focus:ring-accent"
      />
    </div>
  );
}
