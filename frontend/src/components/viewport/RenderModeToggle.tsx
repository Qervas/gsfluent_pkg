import { useState } from "react";

/** Floating viewport pill: switch between Points and Splat rendering.
 *
 *  Dispatches to viser's /mode endpoint. State is local because viser
 *  owns the source of truth — we just optimistically reflect the user's
 *  choice in the toggle while the POST is in flight. If the POST fails
 *  (viser down), the next /state poll in ViserSplatScene would surface
 *  the mismatch.
 *
 *  Positioning: rides the right edge of the viewport at `right-3`.
 *  Phase 3 removed the right-anchored Properties card; the toggle now
 *  parks statically without panel-aware offsets.
 */
export function RenderModeToggle({ splatAvailable }: { splatAvailable: boolean }) {
  const [mode, setMode] = useState<"splat" | "points">("splat");

  const controlUrl = (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined)
    || `http://${location.hostname}:8092`;

  const switchMode = async (next: "splat" | "points") => {
    setMode(next);
    try {
      await fetch(`${controlUrl}/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: next }),
      });
    } catch {
      /* viser unreachable; user will retry */
    }
  };

  const buttonClass = (active: boolean, disabled: boolean) =>
    `px-2 py-1 text-[10px] uppercase tracking-wider font-mono transition-colors ${
      active
        ? "bg-accent text-canvas"
        : disabled
        ? "text-text-muted/40 cursor-not-allowed"
        : "text-text-secondary hover:bg-elevated"
    }`;

  return (
    <div
      className="absolute top-[68px] right-3 z-10 flex border border-border rounded overflow-hidden bg-canvas/85 backdrop-blur"
      title={
        splatAvailable
          ? "Render mode"
          : "Splat rendering requires an active cell"
      }
    >
      <button
        className={buttonClass(mode === "points", false)}
        onClick={() => switchMode("points")}
      >
        Points
      </button>
      <button
        className={buttonClass(mode === "splat" && splatAvailable, !splatAvailable)}
        onClick={() => splatAvailable && switchMode("splat")}
        disabled={!splatAvailable}
      >
        Splat
      </button>
    </div>
  );
}
