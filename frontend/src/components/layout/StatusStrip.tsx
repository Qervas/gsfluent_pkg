import { useStore } from "@/lib/store";
import { deriveMode, modeAccentClass, modeLabel } from "@/lib/derive-mode";
import { ConsoleAccordion } from "@/components/runs/ConsoleAccordion";

// Coarse phase label parsed from the most recent log lines. Used in
// place of the raw simState string so the "running" state can be
// split into the sub-phases the sim actually goes through.
function deriveStage(state: string, logTail: string): string {
  if (state !== "running") return state;
  if (logTail.includes("[PhaseA-SUMMARY]")) return "fuse drain";
  if (logTail.includes("step 2/3") && logTail.includes("fuse")) return "fusing";
  if (logTail.includes("[PhaseA]") || logTail.includes("step 1/3")) return "simulating";
  return "starting (kernel JIT)";
}

// ETA from observed fps since the first frame landed. Returns "—" if
// we haven't seen a frame yet, a `M:SS · fps` string mid-run, and a
// final fps summary at completion.
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
