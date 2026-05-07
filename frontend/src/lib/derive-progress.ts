/**
 * Pure helpers for deriving sim progress UI from the store state.
 *
 * Stage = a coarse phase label parsed from the most recent log lines:
 *   "starting (kernel JIT)" → "simulating" → "fusing" → "fuse drain" →
 *   terminal state passed through.
 *
 * ETA = remaining time computed from observed fps since the first frame.
 *   Returns "—" if no frames yet, "0:00 (done)" at completion.
 */

export function deriveStage(state: string, logTail: string): string {
  if (state !== "running") return state;
  if (logTail.includes("[PhaseA-SUMMARY]")) return "fuse drain";
  if (logTail.includes("step 2/3") && logTail.includes("fuse")) return "fusing";
  if (logTail.includes("[PhaseA]") || logTail.includes("step 1/3")) return "simulating";
  return "starting (kernel JIT)";
}

export function computeEta(
  nFrames: number,
  totalFrames: number,
  firstFrameAt: number | null,
): string {
  if (firstFrameAt === null || nFrames === 0) return "—";
  if (nFrames >= totalFrames) {
    const fps = firstFrameAt
      ? nFrames / Math.max((Date.now() - firstFrameAt) / 1000, 0.001)
      : 0;
    return `0:00 (${fps.toFixed(2)} fps avg)`;
  }
  const elapsed = Math.max((Date.now() - firstFrameAt) / 1000, 0.001);
  const fps = nFrames / elapsed;
  if (fps <= 0) return "computing…";
  const remaining = (totalFrames - nFrames) / fps;
  const m = Math.floor(remaining / 60);
  const s = Math.floor(remaining % 60);
  return `${m}:${s.toString().padStart(2, "0")}  ·  ${fps.toFixed(2)} fps`;
}
