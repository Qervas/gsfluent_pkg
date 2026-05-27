import { useEffect } from "react";
import { Pause, Play, Repeat, RotateCcw } from "lucide-react";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";

/**
 * Minimal transport bar: play/pause, reset-to-start, loop. No scrubber —
 * playback runs entirely in SplatScene's rAF loop, so the bar only carries
 * coarse intent (no per-frame React state, which was the old stutter source).
 * Visible only for sequences with at least 2 frames.
 *
 *   [↻reset] [▶/⏸]  [↻loop]
 *
 * Keyboard: Space = play/pause, L = loop, 0 = reset (skips when focus is in
 * an editable element so we don't fight text entry).
 */
export function PlaybackBar() {
  const { isSequence } = useActiveCell();
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const nFrames = useStore((s) => s.playbackState.n_frames);
  const playing = useStore((s) => s.playing);
  const loop = useStore((s) => s.loop);
  const setPlaying = useStore((s) => s.setPlaying);
  const setLoop = useStore((s) => s.setLoop);
  const requestReset = useStore((s) => s.requestReset);

  const totalFrames = simTotalFrames > 0 ? simTotalFrames : nFrames;
  const visible = isSequence && (totalFrames >= 2 || nFrames >= 2);

  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName?.toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      switch (e.key) {
        case " ": e.preventDefault(); setPlaying(!playing); break;
        case "l": case "L": e.preventDefault(); setLoop(!loop); break;
        case "0": e.preventDefault(); requestReset(); break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, playing, loop, setPlaying, setLoop, requestReset]);

  if (!visible) return null;

  return (
    <div
      className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 px-3 py-1.5 bg-canvas/85 backdrop-blur border border-border rounded text-text-primary"
      role="toolbar"
      aria-label="Playback controls"
    >
      {/* Reset to start */}
      <button
        className="p-1 text-text-secondary hover:text-text-primary hover:bg-elevated rounded transition-colors"
        onClick={() => requestReset()}
        title="Reset to start (0)"
        aria-label="Reset to start"
      >
        <RotateCcw size={14} />
      </button>

      {/* Play / pause */}
      <button
        className="p-1 text-accent hover:bg-elevated rounded transition-colors"
        onClick={() => setPlaying(!playing)}
        title={playing ? "Pause (Space)" : "Play (Space)"}
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? <Pause size={16} /> : <Play size={16} />}
      </button>

      {/* Loop toggle */}
      <button
        className={"p-1 rounded transition-colors hover:bg-elevated " + (loop ? "text-accent" : "text-text-muted")}
        onClick={() => setLoop(!loop)}
        title={loop ? "Loop on (L)" : "Loop off (L)"}
        aria-label={loop ? "Disable loop" : "Enable loop"}
        aria-pressed={loop}
      >
        <Repeat size={14} />
      </button>
    </div>
  );
}
