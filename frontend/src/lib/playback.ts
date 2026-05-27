/**
 * Pure playback helpers for the rAF playback loop.
 *
 * No React, no zustand, no DOM — deterministic functions over primitives so
 * the advance logic lives in exactly one place and stays unit-testable.
 * SplatScene's per-frame rAF callback drives `tickPlayback`; nothing else
 * advances frames.
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

export interface TickResult {
  /** Frame index after this tick. */
  frame: number;
  /** Accumulator carry-over (ms) into the next tick. */
  acc: number;
  /** True when this tick advanced to a new frame (caller must re-render it). */
  advanced: boolean;
  /** True when playback hit the end with loop off (caller should pause). */
  stopped: boolean;
}

/**
 * One wall-clock-accumulator playback step — the pure core of the rAF loop
 * (mirrors spike-spark/src/main.ts). Advances AT MOST one frame per call and
 * resets the accumulator to 0 on advance, so a long stall plays slow rather
 * than skipping frames (strict-sequential / stutter-over-skip invariant).
 */
export function tickPlayback(
  frame: number,
  acc: number,
  dtMs: number,
  intervalMs: number,
  frameCount: number,
  playing: boolean,
  loop: boolean,
): TickResult {
  if (!playing || frameCount < 2) {
    return { frame, acc: 0, advanced: false, stopped: false };
  }
  const next = acc + dtMs;
  if (next < intervalMs) {
    return { frame, acc: next, advanced: false, stopped: false };
  }
  const nf = nextFrame(frame, frameCount, loop);
  if (nf === "stop") {
    return { frame, acc: 0, advanced: false, stopped: true };
  }
  return { frame: nf, acc: 0, advanced: true, stopped: false };
}
