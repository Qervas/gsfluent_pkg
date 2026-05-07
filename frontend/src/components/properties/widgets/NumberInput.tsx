export function NumberInput({
  label,
  value,
  onChange,
  step,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: number;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5" title={hint}>
      <span className="text-text-secondary text-xs flex-1 truncate">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (!Number.isNaN(n)) onChange(n);
        }}
        step={step}
        className="font-mono text-text-primary bg-elevated rounded px-1 w-24 text-right text-xs focus:outline-none focus:ring-1 focus:ring-accent"
      />
    </div>
  );
}
