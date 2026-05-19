import { useEffect } from "react";
import { cn } from "@/lib/cn";

export type PlayState = {
  playing: boolean;
  frame: number;
  total: number;
  fps: number;        // playback speed in frames/s (5/10/30/60)
};

export function PlaybackBar({
  state, setState,
}: {
  state: PlayState;
  setState: (s: PlayState) => void;
}): JSX.Element {
  // Animation loop.
  useEffect(() => {
    if (!state.playing || state.total <= 1) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      const advance = dt * state.fps;
      if (advance >= 1) {
        const next = (state.frame + Math.floor(advance)) % state.total;
        setState({ ...state, frame: next });
        last = now;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state.playing, state.fps, state.total, state.frame, setState]);

  const disabled = state.total <= 1;

  return (
    <div className="flex items-center gap-3 p-2 glass rounded text-xs">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setState({ ...state, playing: !state.playing })}
        className={cn(
          "w-8 h-8 rounded-full flex items-center justify-center transition-colors",
          state.playing ? "bg-accent text-slate-950" : "bg-elevated/80 text-slate-200",
          "disabled:opacity-40",
        )}
        aria-label={state.playing ? "pause" : "play"}
      >
        {state.playing ? "⏸" : "▶"}
      </button>

      <input
        type="range"
        min={0}
        max={Math.max(0, state.total - 1)}
        value={state.frame}
        disabled={disabled}
        onChange={(e) =>
          setState({ ...state, frame: Number(e.currentTarget.value), playing: false })
        }
        className="flex-1 accent-cyan-400"
      />

      <span className="font-mono text-slate-400 w-20 text-right">
        {state.frame + 1}/{state.total}
      </span>

      <select
        value={state.fps}
        onChange={(e) => setState({ ...state, fps: Number(e.currentTarget.value) })}
        disabled={disabled}
        className="bg-elevated/60 border border-border rounded px-2 py-1 text-xs"
      >
        <option value={5}>0.5×</option>
        <option value={10}>1×</option>
        <option value={20}>2×</option>
        <option value={30}>3×</option>
        <option value={60}>6×</option>
      </select>
    </div>
  );
}
