import { useEffect, useRef, useState } from "react";
import {
  Pause,
  Play,
  Repeat,
  SkipBack,
  SkipForward,
} from "lucide-react";
import { useStore, SPEED_X_VALUES, type SpeedX } from "@/lib/store";

/**
 * Persistent transport bar at the bottom of the viewport. Visible only
 * when a playable sequence is active (simRunName set + at least 2 frames
 * landed) — single-frame model previews skip the bar entirely.
 *
 * Spec layout:
 *   [◀◀] [▶/⏸] [▶▶]   ▰▰▰▰▰▱▱▱▱▱   12 / 30   1×▾   ↻
 *
 * All state lives in the zustand `Playback` slice; this component only
 * dispatches actions. Frame-advance ticks are owned by PlaybackDriver,
 * not here.
 */
export function PlaybackBar() {
  const simRunName = useStore((s) => s.simRunName);
  const simState = useStore((s) => s.simState);
  const frameCount = useStore((s) => s.frameXyz.size);
  const currentFrameIdx = useStore((s) => s.currentFrameIdx);
  const playing = useStore((s) => s.playing);
  const speedX = useStore((s) => s.speedX);
  const loop = useStore((s) => s.loop);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);
  const setPlaying = useStore((s) => s.setPlaying);
  const setSpeedX = useStore((s) => s.setSpeedX);
  const setLoop = useStore((s) => s.setLoop);
  const setScrubbing = useStore((s) => s.setScrubbing);
  const stepFrame = useStore((s) => s.stepFrame);

  const [speedOpen, setSpeedOpen] = useState(false);
  const speedRef = useRef<HTMLDivElement | null>(null);

  // Click-outside dismiss for the speed dropdown.
  useEffect(() => {
    if (!speedOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (
        speedRef.current &&
        !speedRef.current.contains(e.target as Node)
      ) {
        setSpeedOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [speedOpen]);

  // Visibility gate: we need an active sequence with >= 2 frames. Hides
  // the bar for the single-frame static-model preview case (simRunName
  // is set but frameXyz.size === 1).
  if (!simRunName || frameCount < 2) return null;

  const isLive = simState === "running";
  const last = frameCount - 1;

  const onScrubChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = parseInt(e.target.value, 10);
    if (Number.isFinite(v)) setCurrentFrame(v);
  };

  // Mouse-down arms `scrubbing` so PlaybackDriver suspends advance while
  // the drag is in progress; mouse-up clears it. Touch end and blur
  // round out the cases where the drag implicitly terminates without a
  // mouse-up event.
  const armScrub = () => setScrubbing(true);
  const releaseScrub = () => setScrubbing(false);

  return (
    <div
      className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-2 px-3 py-1.5 bg-canvas/85 backdrop-blur border border-border rounded text-text-primary"
      role="toolbar"
      aria-label="Playback controls"
    >
      {/* Step back */}
      <button
        className="p-1 text-text-secondary hover:text-text-primary hover:bg-elevated rounded transition-colors"
        onClick={() => stepFrame(-1)}
        title="Previous frame (←)"
        aria-label="Previous frame"
      >
        <SkipBack size={14} />
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

      {/* Step forward */}
      <button
        className="p-1 text-text-secondary hover:text-text-primary hover:bg-elevated rounded transition-colors"
        onClick={() => stepFrame(1)}
        title="Next frame (→)"
        aria-label="Next frame"
      >
        <SkipForward size={14} />
      </button>

      {/* Scrubber */}
      <input
        type="range"
        min={0}
        max={last}
        step={1}
        value={currentFrameIdx}
        onChange={onScrubChange}
        onMouseDown={armScrub}
        onMouseUp={releaseScrub}
        onTouchStart={armScrub}
        onTouchEnd={releaseScrub}
        onBlur={releaseScrub}
        className="playback-scrubber w-56 accent-accent"
        aria-label="Frame scrubber"
      />

      {/* Frame counter */}
      <div className="font-mono text-[11px] tabular-nums text-text-secondary whitespace-nowrap">
        <span className="text-text-primary">{currentFrameIdx}</span>
        <span className="text-text-muted"> / </span>
        <span>{last}</span>
        {isLive && (
          <span className="ml-1 text-accent">(sim running)</span>
        )}
      </div>

      {/* Speed dropdown */}
      <div className="relative" ref={speedRef}>
        <button
          className="px-2 py-0.5 text-[11px] font-mono uppercase tracking-wider text-text-secondary hover:text-text-primary hover:bg-elevated border border-border rounded transition-colors"
          onClick={() => setSpeedOpen((o) => !o)}
          title="Playback speed (, / .)"
          aria-haspopup="menu"
          aria-expanded={speedOpen}
        >
          {formatSpeed(speedX)}
        </button>
        {speedOpen && (
          <div
            className="absolute bottom-full mb-1 right-0 bg-canvas border border-border rounded shadow-lg overflow-hidden z-20"
            role="menu"
          >
            {SPEED_X_VALUES.map((s) => (
              <button
                key={s}
                onClick={() => {
                  setSpeedX(s);
                  setSpeedOpen(false);
                }}
                className={
                  "block w-full px-3 py-1 text-left text-[11px] font-mono tabular-nums transition-colors " +
                  (s === speedX
                    ? "text-accent bg-elevated"
                    : "text-text-secondary hover:text-text-primary hover:bg-elevated")
                }
                role="menuitem"
              >
                {formatSpeed(s)}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Loop toggle */}
      <button
        className={
          "p-1 rounded transition-colors hover:bg-elevated " +
          (loop ? "text-accent" : "text-text-muted")
        }
        onClick={() => setLoop(!loop)}
        title={loop ? "Loop on (L) — click to stop at end" : "Loop off (L) — click to cycle"}
        aria-label={loop ? "Disable loop" : "Enable loop"}
        aria-pressed={loop}
      >
        <Repeat size={14} />
      </button>
    </div>
  );
}

/** Render the speed multiplier — quarter and half show as decimals,
 * integers as plain numbers, all suffixed with `×`. */
function formatSpeed(s: SpeedX): string {
  if (s === 0.25) return "0.25×";
  if (s === 0.5) return "0.5×";
  return `${s}×`;
}
