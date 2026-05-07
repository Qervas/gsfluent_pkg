import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, Pause } from "lucide-react";
import { api } from "@/lib/api";
import { ComparePane } from "./ComparePane";

/** Drives the synchronized slider — a setInterval-based ~24fps tick that
 *  re-fires whenever `playing` or `maxFrames` changes. The ref dance keeps
 *  the latest tick callback fresh without restarting the interval each
 *  frame. */
function useTickerEffect(
  playing: boolean,
  maxFrames: number,
  tick: () => void,
) {
  const tickRef = useRef(tick);
  tickRef.current = tick;
  useEffect(() => {
    if (!playing || maxFrames <= 1) return;
    const id = setInterval(() => tickRef.current(), 1000 / 24);
    return () => clearInterval(id);
  }, [playing, maxFrames]);
}

export function CompareWorkspace() {
  const { data: history = [] } = useQuery({
    queryKey: ["history"],
    queryFn: api.runs.history,
  });
  const [leftRun, setLeftRun] = useState<string | null>(null);
  const [rightRun, setRightRun] = useState<string | null>(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [maxFrames, setMaxFrames] = useState(150);

  // Self-advance frame when playing.
  useTickerEffect(playing, maxFrames, () => {
    setCurrentFrame((f) => (f + 1) % Math.max(1, maxFrames));
  });

  return (
    <div className="h-full flex flex-col">
      {/* Picker bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border text-xs">
        <span className="text-text-muted">Left:</span>
        <select
          value={leftRun ?? ""}
          onChange={(e) => setLeftRun(e.target.value || null)}
          className="bg-canvas border border-border rounded px-2 py-1 text-text-primary"
        >
          <option value="">— pick a run —</option>
          {history.map((h) => (
            <option key={h.run_name} value={h.run_name}>
              {h.run_name}
            </option>
          ))}
        </select>
        <span className="text-text-muted ml-4">Right:</span>
        <select
          value={rightRun ?? ""}
          onChange={(e) => setRightRun(e.target.value || null)}
          className="bg-canvas border border-border rounded px-2 py-1 text-text-primary"
        >
          <option value="">— pick a run —</option>
          {history.map((h) => (
            <option key={h.run_name} value={h.run_name}>
              {h.run_name}
            </option>
          ))}
        </select>
      </div>

      {/* Two panes side-by-side */}
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 border-r border-border relative">
          {leftRun ? (
            <ComparePane
              runName={leftRun}
              currentFrame={currentFrame}
              onFrameCount={setMaxFrames}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-text-muted">
              Pick a run for the left pane.
            </div>
          )}
        </div>
        <div className="flex-1 relative">
          {rightRun ? (
            <ComparePane
              runName={rightRun}
              currentFrame={currentFrame}
              onFrameCount={setMaxFrames}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-text-muted">
              Pick a run for the right pane.
            </div>
          )}
        </div>
      </div>

      {/* Synchronized timeline */}
      <div className="border-t border-border px-3 py-2 flex items-center gap-2 text-xs font-mono">
        <button
          onClick={() => setPlaying(!playing)}
          className="hover:bg-elevated rounded p-0.5 text-accent"
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0, maxFrames - 1)}
          value={currentFrame}
          onChange={(e) => {
            setCurrentFrame(parseInt(e.target.value));
            setPlaying(false);
          }}
          className="flex-1 accent-accent"
        />
        <span className="text-text-muted">
          {currentFrame}/{maxFrames}
        </span>
      </div>
    </div>
  );
}
