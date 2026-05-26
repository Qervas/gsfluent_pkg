import { useEffect } from "react";
import { useStore } from "@/lib/store";

/** Drives currentFrameIdx advancement for sequence playback.
 *
 *  Wall-clock paced model: each iteration bumps currentFrameIdx by one
 *  and waits the inter-frame delay (1000/fpsHint ms scaled by speedX).
 *  SplatScene decodes and pushes each frame in-browser via Spark; when
 *  decode lags, it holds the previous frame instead of skipping (no-skip
 *  invariant). Backpressure is reflected in `pushed_frame` vs `frame`
 *  inside playbackState — the bar displays `pushed_frame` so it never
 *  leads the rendered splat. */
export function PlaybackDriver(): null {
  const playing = useStore((s) => s.playing);
  const scrubbing = useStore((s) => s.scrubbing);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);
  const setPlaying = useStore((s) => s.setPlaying);
  const loop = useStore((s) => s.loop);
  const speedX = useStore((s) => s.speedX);
  const fpsHint = useStore((s) => s.fpsHint);
  const nFrames = useStore((s) => s.playbackState.n_frames);
  const playbackFrame = useStore((s) => s.playbackState.frame);

  useEffect(() => {
    if (!playing || scrubbing) return;
    if (nFrames < 2) return;

    let cancelled = false;
    const delay = (1000 / Math.max(fpsHint, 1)) / speedX;

    const sleep = (ms: number) =>
      new Promise<void>((res) => window.setTimeout(res, ms));

    const tick = async () => {
      while (!cancelled) {
        // Read latest from the store at each iteration so manual
        // scrubs and external setCurrentFrame calls aren't lost.
        const st = useStore.getState();
        const cur = st.currentFrameIdx;
        const lastIdx = st.playbackState.n_frames - 1;
        if (lastIdx < 1) return;
        const next = cur + 1;
        if (next > lastIdx) {
          if (st.loop) setCurrentFrame(0);
          else { setPlaying(false); return; }
        } else {
          setCurrentFrame(next);
        }
        // Wait the inter-frame interval. SplatScene's in-browser decode
        // loop runs independently; it holds frames when decode is slow
        // rather than skipping, so bar pacing stays sane at any speed.
        await sleep(delay);
      }
    };
    void tick();
    return () => { cancelled = true; };
  }, [
    playing, scrubbing, nFrames, playbackFrame,
    setCurrentFrame, setPlaying, loop, speedX, fpsHint,
  ]);

  return null;
}
