export function Vec3Input({
  label,
  value,
  onChange,
  step,
  hint,
}: {
  label: string;
  value: [number, number, number];
  onChange: (v: [number, number, number]) => void;
  step?: number;
  hint?: string;
}) {
  const update = (i: 0 | 1 | 2, n: number) => {
    const next = [...value] as [number, number, number];
    next[i] = n;
    onChange(next);
  };
  return (
    <div className="py-0.5" title={hint}>
      <div className="text-text-secondary text-xs mb-0.5 truncate">{label}</div>
      <div className="flex gap-1">
        {(["x", "y", "z"] as const).map((axis, i) => (
          <input
            key={axis}
            type="number"
            value={value[i]}
            onChange={(e) => {
              const n = parseFloat(e.target.value);
              if (!Number.isNaN(n)) update(i as 0 | 1 | 2, n);
            }}
            step={step}
            className="font-mono text-text-primary bg-elevated rounded px-1 flex-1 text-right text-xs focus:outline-none focus:ring-1 focus:ring-accent"
            title={axis}
          />
        ))}
      </div>
    </div>
  );
}
