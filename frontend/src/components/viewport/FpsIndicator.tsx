import { useEffect, useState } from "react";
import { useStore } from "@/lib/store";

/**
 * Browser-frame FPS counter via requestAnimationFrame. Independent of
 * R3F's render loop — measures the rate at which the browser is
 * actually painting, which under normal conditions is the same as the
 * R3F frame rate. Updates every ~500ms so the number doesn't jitter.
 *
 * Color hint: green when paint cadence is healthy (≥50 fps), amber
 * when degraded (30-49), red when janky (<30) — matches what the user
 * would intuit from feel.
 *
 * Position: top-left below the TopBar. Slides right to clear the
 * left-anchored Outliner card (w-72 + left-3 gap = 300px) when open.
 */
export function FpsIndicator() {
  const [fps, setFps] = useState(0);
  const outlinerOpen = useStore((s) => s.panels.outliner !== "collapsed");

  useEffect(() => {
    let frameCount = 0;
    let last = performance.now();
    let raf = 0;

    const tick = () => {
      frameCount += 1;
      const now = performance.now();
      const elapsed = now - last;
      if (elapsed >= 500) {
        setFps(Math.round((frameCount * 1000) / elapsed));
        frameCount = 0;
        last = now;
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const color =
    fps >= 50 ? "text-success" : fps >= 30 ? "text-warning" : "text-error";

  return (
    <div className={`absolute top-[68px] z-10 px-2 py-1 text-[10px] font-mono uppercase tracking-wider bg-canvas/85 backdrop-blur border border-border rounded flex items-center gap-1.5 transition-[left] duration-panel ease-motion ${outlinerOpen ? "left-[312px]" : "left-3"}`}>
      <span className={color}>●</span>
      <span className="text-text-secondary tabular-nums">{fps}</span>
      <span className="text-text-muted">fps</span>
    </div>
  );
}
