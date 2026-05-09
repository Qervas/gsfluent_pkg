import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { useStore } from "@/lib/store";
import { frameDelayMs, nextFrame } from "@/lib/playback";

/**
 * Single-source-of-truth playback ticker. Mounts inside <Canvas> so it
 * gets the R3F render loop's monotonic clock; replaces the per-renderer
 * inline `useFrame` advance loops that used to live in SplatScene (and
 * implicitly in GaussianSplatScene, which read the same currentFrameIdx).
 *
 * Spec invariant: speedX scales inter-frame DELAY, never the frame index
 * step. 4× still hits every frame, just faster. See `lib/playback.ts`.
 *
 * Pauses while `scrubbing === true` so the user's drag wins — otherwise
 * autoplay would fight the scrubber on every frame.
 */
export function PlaybackDriver() {
  // Wall-clock (well, R3F-clock) of the last frame advance, used as the
  // reference point for the next inter-frame delay check. Reset to 0
  // whenever playback pauses so resuming doesn't fast-forward over the
  // gap.
  const lastAdvanceMs = useRef<number>(0);
  const wasPlaying = useRef<boolean>(false);

  useFrame(({ clock }) => {
    const st = useStore.getState();
    const playing = st.playing;
    if (!playing) {
      wasPlaying.current = false;
      return;
    }
    if (st.scrubbing) {
      // While the user is dragging, don't auto-advance — but also reset
      // lastAdvanceMs so resume is responsive (doesn't burst-advance to
      // catch up to elapsedTime).
      lastAdvanceMs.current = clock.elapsedTime * 1000;
      wasPlaying.current = true;
      return;
    }
    const frameCount = st.frameXyz.size;
    if (frameCount <= 1) return;

    const nowMs = clock.elapsedTime * 1000;
    if (!wasPlaying.current) {
      // Just resumed: reset reference so we don't fire a burst.
      lastAdvanceMs.current = nowMs;
      wasPlaying.current = true;
      return;
    }

    const delay = frameDelayMs(st.fpsHint, st.speedX);
    if (nowMs - lastAdvanceMs.current < delay) return;

    const next = nextFrame(st.currentFrameIdx, frameCount, st.loop);
    if (next === "stop") {
      st.setPlaying(false);
      return;
    }
    st.setCurrentFrame(next);
    lastAdvanceMs.current = nowMs;
  });

  return null;
}
