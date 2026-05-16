import { Slider } from "@/components/ui/slider";
import { HelpIcon } from "./HelpIcon";

/** A 2-line, science-flavored parameter editor.
 *
 *   Young's E                  5,000 (sim)        ?
 *   ├──•──╪─────────•──────────╪─────•──┤
 *      jelly      firm jelly       metal
 *
 *  Top row: label · value · unit · help. Bottom row: slider with
 *  tick marks labeled with reference values (so the user can see where
 *  the current value sits relative to known materials).
 *
 *  `scale="log"` maps the slider position logarithmically — essential
 *  for parameters like Young's modulus that span 4+ orders of magnitude
 *  (linear sliders pin small values to the far-left dead zone).
 *
 *  `markers` is optional. Each marker draws a tick at its `value`
 *  position with a short text label underneath. Useful for showing
 *  "where does <material> sit on this axis."
 */
export type Marker = { value: number; label: string };

type Scale = "linear" | "log";

export function ScientificInput({
  label,
  value,
  onChange,
  min,
  max,
  step,
  unit,
  scale = "linear",
  hint,
  markers,
  format,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  /** Short unit chip rendered next to the value, e.g. "Pa", "°", "kg/m³". */
  unit?: string;
  scale?: Scale;
  hint?: string;
  markers?: Marker[];
  /** Override the rendered value text. Defaults to compact-number formatting. */
  format?: (v: number) => string;
}) {
  const fmt = format ?? defaultFormat;
  return (
    <div className="py-1.5 px-0.5">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-text-secondary text-xs flex-1 truncate flex items-center gap-1">
          <span className="truncate">{label}</span>
          <HelpIcon hint={hint} />
        </span>
        <span className="font-mono text-[11px] text-text-primary tabular-nums">
          {fmt(value)}
        </span>
        {unit && (
          <span className="font-mono text-[10px] text-text-muted">{unit}</span>
        )}
      </div>
      {scale === "log" ? (
        <LogSlider
          value={value}
          onChange={onChange}
          min={min}
          max={max}
          markers={markers}
        />
      ) : (
        <LinearSlider
          value={value}
          onChange={onChange}
          min={min}
          max={max}
          step={step}
          markers={markers}
        />
      )}
    </div>
  );
}

function LinearSlider({
  value,
  onChange,
  min,
  max,
  step,
  markers,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  markers?: Marker[];
}) {
  return (
    <div className="relative">
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => onChange(v[0])}
      />
      {markers && markers.length > 0 && (
        <MarkerStrip
          markers={markers}
          toPos={(m) => (m - min) / (max - min)}
        />
      )}
    </div>
  );
}

function LogSlider({
  value,
  onChange,
  min,
  max,
  markers,
}: {
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  markers?: Marker[];
}) {
  // Slider runs 0..1000 in linear space; we map that to log(value).
  // 1000 steps gives smooth feel without subpixel jitter at the thumb.
  const logMin = Math.log10(min);
  const logMax = Math.log10(max);
  const v = clamp(value, min, max);
  const pos = ((Math.log10(v) - logMin) / (logMax - logMin)) * 1000;
  return (
    <div className="relative">
      <Slider
        value={[pos]}
        min={0}
        max={1000}
        step={1}
        onValueChange={(p) => {
          const t = p[0] / 1000;
          const next = Math.pow(10, logMin + t * (logMax - logMin));
          onChange(roundToSignificant(next, 3));
        }}
      />
      {markers && markers.length > 0 && (
        <MarkerStrip
          markers={markers}
          toPos={(m) => (Math.log10(clamp(m, min, max)) - logMin) / (logMax - logMin)}
        />
      )}
    </div>
  );
}

function MarkerStrip({
  markers,
  toPos,
}: {
  markers: Marker[];
  toPos: (value: number) => number;
}) {
  return (
    <div className="relative h-3.5 mt-0.5 pointer-events-none">
      {markers.map((m, i) => {
        const p = clamp(toPos(m.value), 0, 1) * 100;
        return (
          <div
            key={i}
            className="absolute top-0 -translate-x-1/2 flex flex-col items-center"
            style={{ left: `${p}%` }}
          >
            <div className="w-px h-1.5 bg-text-muted/50" />
            <span className="text-[9px] text-text-muted leading-none whitespace-nowrap">
              {m.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function roundToSignificant(v: number, digits: number): number {
  if (v === 0) return 0;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(v))) - (digits - 1));
  return Math.round(v / mag) * mag;
}

function defaultFormat(v: number): string {
  if (!isFinite(v)) return String(v);
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1e6) return v.toExponential(2).replace("+", "");
  if (a >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (a >= 10) return v.toFixed(1).replace(/\.0$/, "");
  if (a >= 1) return v.toFixed(2).replace(/\.?0+$/, "");
  return v.toPrecision(3).replace(/\.?0+$/, "");
}
