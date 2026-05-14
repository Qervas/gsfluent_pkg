import { useEffect, useRef } from "react";
import { useStore } from "@/lib/store";
import { frameDelayMs, nextFrame } from "@/lib/playback";

/**
 * Single-source-of-truth playback ticker. Runs via requestAnimationFrame
 * so it's renderer-agnostic — the same hook drives Points (R3F) and
 * Splats (PlayCanvas) modes. Earlier versions used R3F's useFrame, which
 * tied the ticker to the R3F render loop and broke when the Splats mode
 * mounts a non-R3F canvas instead.
 *
 * Spec invariant: speedX scales inter-frame DELAY, never the frame index
 * step. 4× still hits every frame, just faster. See `lib/playback.ts`.
 *
 * Pauses while `scrubbing === true` so the user's drag wins — otherwise
 * autoplay would fight the scrubber on every frame.
 */
export function usePlaybackTicker(): void {
  // performance.now()-based reference point for the next inter-frame
  // delay check. Reset to 0 whenever playback pauses so resuming doesn't
  // fast-forward over the gap.
  const lastAdvanceMs = useRef<number>(0);
  const wasPlaying = useRef<boolean>(false);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const st = useStore.getState();
      const playing = st.playing;
      if (!playing) {
        wasPlaying.current = false;
        raf = requestAnimationFrame(tick);
        return;
      }
      const nowMs = performance.now();
      if (st.scrubbing) {
        lastAdvanceMs.current = nowMs;
        wasPlaying.current = true;
        raf = requestAnimationFrame(tick);
        return;
      }
      const frameCount = st.frameXyz.size;
      if (frameCount <= 1) {
        raf = requestAnimationFrame(tick);
        return;
      }
      if (!wasPlaying.current) {
        // Just resumed: reset reference so we don't fire a burst.
        lastAdvanceMs.current = nowMs;
        wasPlaying.current = true;
        raf = requestAnimationFrame(tick);
        return;
      }
      const delay = frameDelayMs(st.fpsHint, st.speedX);
      if (nowMs - lastAdvanceMs.current >= delay) {
        const next = nextFrame(st.currentFrameIdx, frameCount, st.loop);
        if (next === "stop") {
          st.setPlaying(false);
        } else {
          st.setCurrentFrame(next);
          lastAdvanceMs.current = nowMs;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);
}

/**
 * Back-compat shim: existing JSX like `<PlaybackDriver />` keeps working
 * even though the implementation moved to a hook. Mount it anywhere in
 * the React tree (no longer needs to be inside a `<Canvas>`).
 */
export function PlaybackDriver() {
  usePlaybackTicker();
  return null;
}
