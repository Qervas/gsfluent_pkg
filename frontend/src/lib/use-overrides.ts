import { useMemo } from "react";
import { useStore } from "./store";

/** Override engine consumer hook.
 *
 *  Returns the merged effective config, the raw overrides map, helpers
 *  to set/clear/reset overrides, and a derived `isOverridden(key)`
 *  predicate the param widgets use to decide accent rendering.
 *
 *  Effective is the *only* value that goes to the sim runner — neither
 *  baseline nor overrides individually are valid sim input. */
export function useOverrides() {
  const baseline      = useStore((s) => s.simRecipeBaseline);
  const overrides     = useStore((s) => s.simOverrides);
  const setOverride   = useStore((s) => s.setOverride);
  const clearOverride = useStore((s) => s.clearOverride);
  const clearAll      = useStore((s) => s.clearAllOverrides);

  // Recomputed on every store change to baseline or overrides. Cheap
  // because recipes are ~50 fields. Memoized so component identity is
  // stable when nothing changed.
  const effective = useMemo(
    () => (baseline ? { ...baseline, ...overrides } : { ...overrides }),
    [baseline, overrides],
  );

  const isOverridden = (key: string): boolean =>
    Object.prototype.hasOwnProperty.call(overrides, key);

  const baselineValue = (key: string): unknown =>
    baseline ? baseline[key] : undefined;

  return {
    effective,
    overrides,
    baseline,
    overrideCount: Object.keys(overrides).length,
    isOverridden,
    baselineValue,
    setOverride,
    clearOverride,
    clearAllOverrides: clearAll,
  };
}

/** Convenience wrapper for property panels: returns the merged effective
 *  config as `data` plus a `setField(key, value)` that routes through
 *  overrides. Returns `null` when no recipe is active so the caller can
 *  early-return with one check. */
export function usePanelData(): {
  data: Record<string, unknown>;
  setField: (key: string, value: unknown) => void;
} | null {
  const { effective, setOverride } = useOverrides();
  const name = useStore((s) => s.activeRecipeName);
  if (!name || !effective) return null;
  return {
    data: effective,
    setField: (key, value) => setOverride(key, value),
  };
}
