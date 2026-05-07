import { useStore } from "@/lib/store";
import { deriveStage, computeEta } from "@/lib/derive-progress";
import { deriveMode, modeAccentClass, modeLabel } from "@/lib/derive-mode";
import { ConsoleAccordion } from "@/components/runs/ConsoleAccordion";

export function StatusStrip() {
  const simState = useStore((s) => s.simState);
  const simNFrames = useStore((s) => s.simNFrames);
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const simLog = useStore((s) => s.simLog);
  const simFirstFrameAt = useStore((s) => s.simFirstFrameAt);
  const simRunName = useStore((s) => s.simRunName);
  const staticAttrs = useStore((s) => s.staticAttrs);
  const frameCount = useStore((s) => s.frameXyz.size);

  const mode = deriveMode(simState, simRunName, frameCount);

  // Model preview: static layout, no progress / ETA / frame counter.
  if (mode.kind === "model_preview") {
    const n = staticAttrs?.n ?? 0;
    return (
      <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted shrink-0 font-mono relative">
        <span className={modeAccentClass(mode)}>●</span>
        <span className="capitalize">{modeLabel(mode)}</span>
        <span className="text-text-muted">·</span>
        <span>{mode.modelName}</span>
        <span className="text-text-muted">·</span>
        <span>{n.toLocaleString()} splats</span>
        <span className="ml-auto flex items-center gap-3">
          <span className="text-text-muted">⌘K</span>
          <ConsoleAccordion />
        </span>
      </div>
    );
  }

  // Otherwise: existing layout for running/replay/idle.
  const tail = simLog.slice(-80).join("\n");
  const stage = deriveStage(simState, tail);
  const pct = simTotalFrames > 0
    ? Math.min(100, (100 * simNFrames) / simTotalFrames)
    : 0;
  const eta = simState === "running"
    ? computeEta(simNFrames, simTotalFrames, simFirstFrameAt)
    : simState === "done"
    ? "0:00 (complete)"
    : "—";

  return (
    <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted shrink-0 font-mono relative">
      <span className={modeAccentClass(mode)}>●</span>
      <span className="capitalize w-32 truncate">{stage}</span>
      <div className="flex-1 max-w-md h-1 bg-elevated rounded overflow-hidden">
        <div
          className="h-full bg-accent transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span>{simNFrames}/{simTotalFrames}</span>
      <span className="ml-2">{eta}</span>
      <span className="ml-auto flex items-center gap-3">
        <span className="text-text-muted">⌘K</span>
        <ConsoleAccordion />
      </span>
    </div>
  );
}
