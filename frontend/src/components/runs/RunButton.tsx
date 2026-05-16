import { Play, Check, X, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";

/** Run button with five visual states, consolidating what used to be
 *  split across the StatusPill + StatusStrip + console. One source of
 *  truth for "what's the sim doing right now":
 *
 *    1. idle, no model+recipe  → grey, hint: "Load model + recipe to run"
 *    2. idle, ready            → accent gradient + faint pulse + "Run"
 *                                 (+ cost preview when we know it)
 *    3. running                → orange + spinner + percent + ETA + "Cancel"
 *    4. done, just finished    → green flash for 2 s, then "Run again"
 *    5. error                  → red + inline error msg
 *
 *  Why one component: keeps the affordance + the progress reporting
 *  next to each other. The user always looks at the button to know
 *  what's happening.
 */
export function RunButton({ subscribe }: { subscribe: (run_name: string) => void }) {
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const { effective } = useOverrides();
  const simState = useStore((s) => s.simState);
  const simRunName = useStore((s) => s.simRunName);
  const simNFrames = useStore((s) => s.simNFrames);
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const simFirstFrameAt = useStore((s) => s.simFirstFrameAt);
  const resetForNewRun = useStore((s) => s.resetForNewRun);
  const runBlockedByJson = useStore((s) => s.runBlockedByJson);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Tracks the "green flash for 2 s after done" window so the just-
  // finished state is visually distinct from later idles on the same run.
  const [justDone, setJustDone] = useState(false);
  const prevStateRef = useRef(simState);

  useEffect(() => {
    if (prevStateRef.current === "running" && simState === "done") {
      setJustDone(true);
      const t = setTimeout(() => setJustDone(false), 2000);
      return () => clearTimeout(t);
    }
    if (simState === "error") {
      // Error state surfaces but we don't auto-clear; user has to
      // re-click Run to dismiss.
    }
    prevStateRef.current = simState;
  }, [simState]);

  const ready =
    !!activeModel && !!activeRecipeName && !!activeRecipeData;

  const onRun = async () => {
    if (!ready || simState === "running" || busy || runBlockedByJson) return;
    setBusy(true);
    setError(null);
    try {
      const ts = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
      const baseName = activeRecipeName!.replace(/^★ /, "");
      const run_name = `${activeModel!.name}_${baseName}_${ts}`;
      resetForNewRun(run_name);
      await api.runs.start({
        run_name,
        model_path: activeModel!.path,
        recipe_data: effective,
        recipe_source: activeRecipeName!,
        particles: 200_000,
      });
      subscribe(run_name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!simRunName) return;
    setBusy(true);
    try {
      const all = await api.runs.list();
      const r = all.find((x) => x.name === simRunName);
      if (r) await api.runs.cancel(r.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // ---- State 3: running ----
  if (simState === "running") {
    const pct = simTotalFrames > 0
      ? Math.min(100, Math.round((100 * simNFrames) / simTotalFrames))
      : 0;
    const eta = computeEta(simNFrames, simTotalFrames, simFirstFrameAt);
    return (
      <button
        type="button"
        onClick={onCancel}
        disabled={busy}
        aria-label={`Cancel sim (${pct}%)`}
        className="relative inline-flex items-center gap-2 px-3 py-1.5 rounded-md
                   bg-warning/15 border border-warning/40 text-warning
                   text-xs font-medium hover:bg-warning/20
                   focus:outline-none focus-visible:ring-2 focus-visible:ring-warning/40
                   transition-colors duration-fast disabled:opacity-50"
      >
        <Loader2 size={11} className="animate-spin" />
        <span className="font-mono tabular-nums">{pct}%</span>
        {eta && <span className="text-warning/70 font-mono text-xxs">{eta}</span>}
        <span className="border-l border-warning/30 pl-2 ml-1">Cancel</span>
      </button>
    );
  }

  // ---- State 4: just finished (green flash) ----
  if (justDone) {
    return (
      <button
        type="button"
        onClick={onRun}
        disabled={!ready || busy}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md
                   bg-success/15 border border-success/40 text-success
                   text-xs font-medium
                   focus:outline-none focus-visible:ring-2 focus-visible:ring-success/40
                   animate-pulse"
      >
        <Check size={11} />
        Done · Run again
      </button>
    );
  }

  // ---- State 5: error ----
  if (simState === "error" || error) {
    return (
      <button
        type="button"
        onClick={onRun}
        disabled={busy}
        title={error ?? "Sim errored — click to retry"}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md
                   bg-error/15 border border-error/40 text-error
                   text-xs font-medium hover:bg-error/20
                   focus:outline-none focus-visible:ring-2 focus-visible:ring-error/40
                   transition-colors duration-fast"
      >
        <X size={11} />
        Error · Retry
      </button>
    );
  }

  // ---- State 2: idle, ready ----
  if (ready) {
    return (
      <button
        type="button"
        onClick={onRun}
        disabled={busy || runBlockedByJson}
        title={
          runBlockedByJson
            ? "Recipe JSON has a parse error"
            : "Submit a sim run (200k particles)"
        }
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md
                   bg-gradient-to-br from-accent to-cyan-600 text-canvas
                   font-semibold text-xs shadow-accent-glow
                   hover:from-accent hover:to-cyan-500
                   focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40
                   transition-all duration-fast disabled:opacity-50"
      >
        <Play size={11} />
        Run
      </button>
    );
  }

  // ---- State 1: idle, not ready ----
  const missing: string[] = [];
  if (!activeModel) missing.push("model");
  if (!activeRecipeName) missing.push("recipe");
  const tooltip = `Load a ${missing.join(" + ")} to run a sim.`;
  return (
    <button
      type="button"
      disabled
      title={tooltip}
      aria-label={tooltip}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md
                 bg-elevated/60 border border-border/40
                 text-text-muted text-xs font-medium cursor-not-allowed"
    >
      <Play size={11} />
      Run
    </button>
  );
}

/** ETA from observed fps since the first frame landed. Returns null
 *  if we don't have enough data to compute. */
function computeEta(
  nFrames: number,
  totalFrames: number,
  firstFrameAt: number | null,
): string | null {
  if (firstFrameAt === null || nFrames === 0) return null;
  const elapsed = Math.max((Date.now() - firstFrameAt) / 1000, 0.001);
  const fps = nFrames / elapsed;
  if (nFrames >= totalFrames || fps <= 0) return null;
  const remaining = (totalFrames - nFrames) / fps;
  const m = Math.floor(remaining / 60);
  const s = Math.floor(remaining % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
