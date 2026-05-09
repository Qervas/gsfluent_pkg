/**
 * Pure playback helpers for the Phase 3 transport bar / driver.
 *
 * No React, no zustand, no DOM — these are deterministic functions over
 * primitives so the driver and the bar stay testable and the advance
 * logic lives in exactly one place. Both the PlaybackDriver and the
 * keyboard shortcut handlers go through them.
 */

/**
 * Snap `idx` into [0, frameCount - 1]. `frameCount === 0` clamps to 0
 * (rendering a no-frames sequence is a non-event — the bar is hidden).
 */
export function clampFrame(idx: number, frameCount: number): number {
  if (frameCount <= 0) return 0;
  if (idx < 0) return 0;
  if (idx >= frameCount) return frameCount - 1;
  return idx;
}

/**
 * Compute the next frame index. Returns `"stop"` when looping is off and
 * we've hit the end — the caller should pause playback in that case.
 */
export function nextFrame(
  idx: number,
  frameCount: number,
  loop: boolean,
): number | "stop" {
  if (frameCount <= 1) return "stop";
  const last = frameCount - 1;
  if (idx >= last) {
    return loop ? 0 : "stop";
  }
  return idx + 1;
}

/**
 * Inter-frame delay in milliseconds for the given fps_hint and speed
 * multiplier. 1× = `1000 / fpsHint` (default 24 fps → ~41.7 ms). Higher
 * speedX shrinks the delay; lower speedX stretches it. The frame index
 * step is always 1 — speedX never skips frames, only changes pacing.
 */
export function frameDelayMs(fpsHint: number, speedX: number): number {
  const fps = fpsHint > 0 ? fpsHint : 24;
  const sx = speedX > 0 ? speedX : 1;
  return 1000 / (fps * sx);
}
