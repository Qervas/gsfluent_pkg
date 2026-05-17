import { useEffect, useRef, useState } from "react";
import { ChevronUp } from "lucide-react";
import { useStore } from "@/lib/store";
import { deriveMode, modeAccentClass, modeLabel } from "@/lib/derive-mode";

/** Floating status pill — replaces the fixed bottom StatusStrip.
 *
 * Sits at the bottom-left of the viewport as a glass-card. Carries
 * the same payload the strip did (mode dot, stage/progress/frames/
 * ETA, ⌘K hint, console toggle) but in a compact pill so it doesn't
 * eat a full row.
 *
 * The console drawer is anchored above the pill — full-width across
 * the viewport when open — instead of absolute-positioned inside the
 * pill, because a pill can't host a 72px-tall drawer.
 *
 * Auto-hides nothing for now; the user wanted the same info in a
 * floating form, not gated visibility.
 */
function deriveStage(state: string, logTail: string): string {
  if (state !== "running") return state;
  if (logTail.includes("[PhaseA-SUMMARY]")) return "fuse drain";
  if (logTail.includes("step 2/3") && logTail.includes("fuse")) return "fusing";
  if (logTail.includes("[PhaseA]") || logTail.includes("step 1/3")) return "simulating";
  return "starting (kernel JIT)";
}

function computeEta(
  nFrames: number,
  totalFrames: number,
  firstFrameAt: number | null,
): string {
  if (firstFrameAt === null || nFrames === 0) return "—";
  const elapsed = Math.max((Date.now() - firstFrameAt) / 1000, 0.001);
  const fps = nFrames / elapsed;
  if (nFrames >= totalFrames) return `0:00 (${fps.toFixed(2)} fps avg)`;
  if (fps <= 0) return "computing…";
  const remaining = (totalFrames - nFrames) / fps;
  const m = Math.floor(remaining / 60);
  const s = Math.floor(remaining % 60);
  return `${m}:${s.toString().padStart(2, "0")}  ·  ${fps.toFixed(2)} fps`;
}

export function StatusPanel() {
  const simState = useStore((s) => s.simState);
  const simNFrames = useStore((s) => s.simNFrames);
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const simLog = useStore((s) => s.simLog);
  const simFirstFrameAt = useStore((s) => s.simFirstFrameAt);
  const simRunName = useStore((s) => s.simRunName);
  const staticAttrs = useStore((s) => s.staticAttrs);
  const frameCount = useStore((s) => s.frameXyz.size);
  // Track the unified left rail so the console drawer doesn't collide
  // with it. The right side has no glass card after Phase 3, so only
  // the left-side reactivity is needed.
  const outlinerOpen = useStore((s) => s.panels.outliner !== "collapsed");

  const [consoleOpen, setConsoleOpen] = useState(false);
  const consoleRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (consoleOpen && consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [simLog, consoleOpen]);

  const mode = deriveMode(simState, simRunName, frameCount);

  const isPreview = mode.kind === "model_preview";
  const tail = simLog.slice(-80).join("\n");
  const stage = isPreview ? "model preview" : deriveStage(simState, tail);
  const pct = simTotalFrames > 0
    ? Math.min(100, (100 * simNFrames) / simTotalFrames)
    : 0;
  const eta = simState === "running"
    ? computeEta(simNFrames, simTotalFrames, simFirstFrameAt)
    : simState === "done"
    ? "0:00 (complete)"
    : "—";

  return (
    <>
      {/* Console drawer — fixed band above the pill when open. Clears
          the unified left rail (w-80 + 12px left-3 + 12px gap = 344px)
          when open; extends to `left-3` when the rail is collapsed.
          The right edge stays at `right-3` since Phase 3 dropped the
          right-anchored Properties card. */}
      {consoleOpen && (
        <div
          className={`fixed bottom-14 right-3 h-72 z-30 glass-card overflow-hidden flex flex-col transition-[left] duration-panel ease-motion ${
            outlinerOpen ? "left-[344px]" : "left-3"
          }`}
          role="region"
          aria-label="Run console"
        >
          <div
            ref={consoleRef}
            className="flex-1 overflow-auto font-mono text-[11px] p-2 leading-tight whitespace-pre-wrap"
          >
            {simLog.length === 0 ? (
              <span className="text-text-muted">(no output yet)</span>
            ) : (
              simLog.map((line, i) => (
                <div key={i} className="text-text-primary">
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Floating pill — bottom-right corner. The right edge of the
          viewport has no glass card after Phase 3, so the pill sits
          freely at `right-3 bottom-3`. */}
      <div
        className="fixed bottom-3 right-3 z-40 glass-card px-3 h-9 flex items-center gap-2 text-xs text-text-muted font-mono"
        role="status"
        aria-label="Run status"
      >
        <span className={modeAccentClass(mode)}>●</span>

        {isPreview ? (
          <>
            <span className="capitalize">{modeLabel(mode)}</span>
            <span className="text-text-muted">·</span>
            <span className="truncate max-w-[180px]">{mode.modelName}</span>
            <span className="text-text-muted">·</span>
            <span>{(staticAttrs?.n ?? 0).toLocaleString()} splats</span>
          </>
        ) : (
          <>
            <span className="capitalize w-28 truncate">{stage}</span>
            <div className="w-32 h-1 bg-elevated rounded overflow-hidden shrink-0">
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="tabular-nums">{eta}</span>
          </>
        )}

        <span className="text-text-muted ml-2 pl-2 border-l border-border/40">
          ⌘K
        </span>
        <button
          onClick={() => setConsoleOpen((o) => !o)}
          className="flex items-center gap-1 hover:text-text-primary"
          title={consoleOpen ? "Hide console" : "Show console"}
          aria-expanded={consoleOpen}
        >
          <ChevronUp
            size={11}
            className={
              "transition-transform duration-fast " +
              (consoleOpen ? "rotate-180" : "")
            }
          />
          console
        </button>
      </div>
    </>
  );
}
