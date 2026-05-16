import { useStore } from "@/lib/store";

/**
 * Floating viewport pill: switch between the lightweight Three.js Points
 * pipeline and the proper anisotropic Gaussian-splat renderer.
 *
 * "Splat" is greyed out + locked to "Points" when there's no static model
 * loaded — for sim runs, per-frame position streaming requires the points
 * path (the splat library can't keep up with per-frame buffer updates).
 *
 * Positioning: rides the right edge of the viewport, but slides left to
 * clear the Properties glass card when it's expanded — otherwise the
 * 80-tw-wide panel covers this toggle (panel is z-20, pill is z-10).
 */
export function RenderModeToggle({ splatAvailable }: { splatAvailable: boolean }) {
  const renderMode = useStore((s) => s.renderMode);
  const setRenderMode = useStore((s) => s.setRenderMode);
  const propertiesOpen = useStore(
    (s) => s.activeRecipeData != null && s.panels.properties !== "collapsed",
  );

  const buttonClass = (active: boolean, disabled: boolean) =>
    `px-2 py-1 text-[10px] uppercase tracking-wider font-mono transition-colors ${
      active
        ? "bg-accent text-canvas"
        : disabled
        ? "text-text-muted/40 cursor-not-allowed"
        : "text-text-secondary hover:bg-elevated"
    }`;

  // Properties card lives at `right-3 w-80` (= 320px) → its left edge is
  // 332px from the viewport right edge. Park the toggle 12px to the
  // left of that so it sits next to the panel, not under it.
  const rightOffset = propertiesOpen ? "right-[344px]" : "right-3";

  return (
    <div
      className={`absolute top-[68px] ${rightOffset} z-10 flex border border-border rounded overflow-hidden bg-canvas/85 backdrop-blur transition-[right] duration-panel ease-motion`}
      title={
        splatAvailable
          ? "Render mode"
          : "Splat rendering is available for static model preview only — sim runs use Points"
      }
    >
      <button
        className={buttonClass(renderMode === "points", false)}
        onClick={() => setRenderMode("points")}
      >
        Points
      </button>
      <button
        className={buttonClass(
          renderMode === "splat" && splatAvailable,
          !splatAvailable,
        )}
        onClick={() => splatAvailable && setRenderMode("splat")}
        disabled={!splatAvailable}
      >
        Splat
      </button>
    </div>
  );
}
