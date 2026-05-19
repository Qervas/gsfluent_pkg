import { Eye, EyeOff } from "lucide-react";
import { useStore } from "@/lib/store";

/** Manual on/off toggle for the viser splat iframe.
 *
 * Mounted in the viewport top-right. When the user flips this off the
 * iframe unmounts entirely (no WebGL state, no /state polling, no
 * sorter-WASM running), which is the escape hatch when viser crashes
 * or NaN's mid-session. Persisted across reloads via localStorage. */
export function ViserToggle() {
  const enabled = useStore((s) => s.viserEnabled);
  const set = useStore((s) => s.setViserEnabled);
  return (
    <button
      onClick={() => set(!enabled)}
      className="absolute top-3 right-3 z-30 glass-card h-8 px-2.5 flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary"
      title={enabled ? "Disable splat viewer (kill iframe)" : "Enable splat viewer"}
      aria-pressed={enabled}
    >
      {enabled ? <Eye size={13} /> : <EyeOff size={13} />}
      <span>Splats {enabled ? "on" : "off"}</span>
    </button>
  );
}
