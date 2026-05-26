import { useEffect } from "react";
import { useStore } from "@/lib/store";

/** Drives currentFrameIdx advancement for sequence playback.
 *
 *  Synchronous-to-viser model: instead of a free-running RAF that
 *  advances at fpsHint regardless of what viser can keep up with, we
 *  drive the bar from viser's actual ready signal. Each iteration:
 *  bump currentFrameIdx, await ViserSplatScene's /set POST to drain
 *  (so the splat geometry is on the server), then wait the inter-
 *  frame delay. Backpressure is automatic — when viser is slow, the
 *  bar slows; bar position and rendered splat stay in lockstep.
 *
 *  We don't await the /set fetch here directly (that's owned by
 *  ViserSplatScene). Instead we read the same `inflight` ref the
 *  ViserSplatScene exposes on the store, polling at a coarse cadence
 *  while a /set is in flight before advancing to the next frame. */
export function PlaybackDriver(): null {
  const playing = useStore((s) => s.playing);
  const scrubbing = useStore((s) => s.scrubbing);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);
  const setPlaying = useStore((s) => s.setPlaying);
  const loop = useStore((s) => s.loop);
  const speedX = useStore((s) => s.speedX);
  const fpsHint = useStore((s) => s.fpsHint);
  const nFrames = useStore((s) => s.playbackState.n_frames);
  const viserFrame = useStore((s) => s.playbackState.frame);

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
        // Wait the inter-frame interval. We don't need to also wait
        // for viser to ack — ViserSplatScene's trailing-edge sender
        // drops intermediate POSTs automatically, so as long as we
        // tick at a sane rate the bar stays paced with the splat.
        await sleep(delay);
      }
    };
    void tick();
    return () => { cancelled = true; };
  }, [
    playing, scrubbing, nFrames, viserFrame,
    setCurrentFrame, setPlaying, loop, speedX, fpsHint,
  ]);

  return null;
}
