import { HelpIcon } from "./HelpIcon";

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
    <div className="py-0.5">
      <div className="text-text-secondary text-xs mb-0.5 truncate flex items-center gap-1">
        <span className="truncate">{label}</span>
        <HelpIcon hint={hint} />
      </div>
      <div className="flex gap-1">
        {(["x", "y", "z"] as const).map((axis, i) => (
          <label
            key={axis}
            className="relative flex-1 min-w-0 flex items-center"
            title={axis}
          >
            {/* Axis prefix so the user can see which channel each input
                edits without hovering. Pointer-events-none so the label
                doesn't steal focus from the input. */}
            <span className="absolute left-1 text-[9px] text-text-muted font-mono uppercase pointer-events-none">
              {axis}
            </span>
            <input
              type="number"
              value={value[i]}
              onChange={(e) => {
                const n = parseFloat(e.target.value);
                if (!Number.isNaN(n)) update(i as 0 | 1 | 2, n);
              }}
              step={step}
              /* min-w-0 lets flex-1 shrink the input below the browser's
                 default ~80px min-width for `<input type=number>` — without
                 it, three side-by-side inputs overflow their flex row and
                 only the first is visible inside a 240-ish px panel. */
              className="font-mono text-text-primary bg-elevated rounded pl-4 pr-1 w-full min-w-0 text-right text-xs focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </label>
        ))}
      </div>
    </div>
  );
}
