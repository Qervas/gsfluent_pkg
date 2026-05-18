import { useEffect, useRef } from "react";
import { useStore } from "@/lib/store";

/** Drives currentFrameIdx advancement for sequence playback. The
 *  actual position rendering happens in viser; this hook just keeps
 *  the index ticking and forwards it via ViserSplatScene's effect.
 *
 *  Buffer-awareness: viser server-side clamps frame to the cell's
 *  n_frames, so the client never has to worry about advancing past
 *  it. We just read n_frames from viserState (kept fresh by
 *  ViserSplatScene's /state poll) and stop / loop at the boundary. */
export function PlaybackDriver(): null {
  const playing = useStore((s) => s.playing);
  const scrubbing = useStore((s) => s.scrubbing);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);
  const setPlaying = useStore((s) => s.setPlaying);
  const loop = useStore((s) => s.loop);
  const speedX = useStore((s) => s.speedX);
  const fpsHint = useStore((s) => s.fpsHint);
  const nFrames = useStore((s) => s.viserState.n_frames);

  // The RAF loop reads `currentFrameIdx` every tick. If we put it in
  // the effect's dep array, each setCurrentFrame would tear down and
  // rebuild the entire loop (and reset `last = performance.now()`),
  // so the `delay` gate never fires — frames advance on every rAF tick
  // (~60 fps) instead of at the requested fpsHint × speedX cadence.
  // Reading through a ref decouples the closure from re-renders.
  const frameRef = useRef(0);
  useEffect(() => {
    const unsub = useStore.subscribe((s) => {
      frameRef.current = s.currentFrameIdx;
    });
    return unsub;
  }, []);

  useEffect(() => {
    if (!playing || scrubbing) return;
    if (nFrames < 2) return;

    let raf = 0;
    let last = performance.now();
    // delay per frame = (1000 / fpsHint) / speedX ms.
    const delay = (1000 / Math.max(fpsHint, 1)) / speedX;

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      if (now - last < delay) return;
      last = now;
      const lastIdx = nFrames - 1;
      const next = frameRef.current + 1;
      if (next > lastIdx) {
        if (loop) setCurrentFrame(0);
        else setPlaying(false);
      } else {
        setCurrentFrame(next);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, scrubbing, nFrames, setCurrentFrame, setPlaying, loop, speedX, fpsHint]);

  return null;
}
