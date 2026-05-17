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
  const simTotalFrames = useStore((s) => s.simTotalFrames);
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

  // Scoped keyboard layer — only active when the playback bar is on
  // screen. The button `title`s advertise these shortcuts; this is the
  // wiring. Skips when the user is in any editable element (palette
  // input, recipe name prompt, etc.) so we don't fight text entry.
  useEffect(() => {
    const totalKnown = simTotalFrames > 0 ? simTotalFrames : frameCount;
    if (!simRunName || (totalKnown < 2 && frameCount < 2)) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName?.toUpperCase();
      const editable =
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
        t?.isContentEditable === true;
      if (editable) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case " ":
          e.preventDefault();
          setPlaying(!playing);
          break;
        case "ArrowLeft":
          e.preventDefault();
          stepFrame(-1);
          break;
        case "ArrowRight":
          e.preventDefault();
          stepFrame(1);
          break;
        case "l":
        case "L":
          e.preventDefault();
          setLoop(!loop);
          break;
        case ",": {
          e.preventDefault();
          const i = SPEED_X_VALUES.indexOf(speedX);
          if (i > 0) setSpeedX(SPEED_X_VALUES[i - 1]);
          break;
        }
        case ".": {
          e.preventDefault();
          const i = SPEED_X_VALUES.indexOf(speedX);
          if (i < SPEED_X_VALUES.length - 1) setSpeedX(SPEED_X_VALUES[i + 1]);
          break;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    simRunName,
    simTotalFrames,
    frameCount,
    playing,
    loop,
    speedX,
    setPlaying,
    setLoop,
    setSpeedX,
    stepFrame,
  ]);

  // Prefer the server-authoritative total so the scrubber spans the
  // full range from the start of streaming. Fall back to the loaded
  // count for orphan sequences with no metadata.
  const totalFrames = simTotalFrames > 0 ? simTotalFrames : frameCount;
  const loadedFrames = frameCount;          // how many frames have streamed in
  const lastIdx = Math.max(totalFrames - 1, 0);

  // Visibility gate: bar shows once we know the run has more than one
  // frame — either from the server total or by loaded count. Hides the
  // single-frame static-model preview (simRunName set + total=1).
  if (!simRunName || (totalFrames < 2 && frameCount < 2)) return null;

  const isLive = simState === "running";

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

      {/* Scrubber with buffer overlay showing loaded fraction */}
      <div className="relative w-56 flex items-center">
        <div
          className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-1 bg-elevated/60 rounded pointer-events-none overflow-hidden"
          aria-hidden
        >
          {/* Loaded buffer — accent at low opacity, ends at the most-recent loaded frame */}
          <div
            className="h-full bg-accent/30"
            style={{
              width: `${totalFrames > 0 ? (loadedFrames / totalFrames) * 100 : 100}%`,
            }}
          />
        </div>
        <input
          type="range"
          min={0}
          max={lastIdx}
          step={1}
          value={currentFrameIdx}
          onChange={onScrubChange}
          onMouseDown={armScrub}
          onMouseUp={releaseScrub}
          onTouchStart={armScrub}
          onTouchEnd={releaseScrub}
          onBlur={releaseScrub}
          className="playback-scrubber relative w-full accent-accent"
          aria-label="Frame scrubber"
        />
      </div>

      {/* Frame counter */}
      <div className="font-mono text-[11px] tabular-nums text-text-secondary whitespace-nowrap">
        <span className="text-text-primary">{currentFrameIdx}</span>
        <span className="text-text-muted"> / </span>
        <span>{lastIdx}</span>
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
