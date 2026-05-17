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
      // Re-fetch the recipe right before dispatching so any server-side
      // edits since the user picked it (recipe file patched, another
      // user updated, etc.) override the stale baseline. We then
      // re-merge with the in-memory overrides so the user's per-run
      // tweaks survive the refresh.
      let baseToSend: Record<string, unknown> = effective;
      try {
        const fresh = await api.recipes.get(activeRecipeName!);
        const overrides = useStore.getState().simOverrides;
        baseToSend = { ...fresh.data, ...overrides };
        // Update the store's baseline so the Form/JSON view reflects
        // the fresh recipe too. Doesn't clobber overrides — the user's
        // tweaks stay in simOverrides.
        useStore.getState().setSimRecipeBaseline(
          JSON.parse(JSON.stringify(fresh.data)),
        );
        // setSimRecipeBaseline clears overrides per its implementation;
        // restore them since we want the merged dispatch + UI continuity.
        for (const [k, v] of Object.entries(overrides)) {
          useStore.getState().setOverride(k, v);
        }
      } catch {
        // Recipe fetch failed (network blip, recipe deleted) — fall
        // back to the stale in-memory baseline. The server's own
        // validation will reject if the recipe truly doesn't exist.
      }

      const ts = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
      const baseName = activeRecipeName!.replace(/^★ /, "");
      const run_name = `${activeModel!.name}_${baseName}_${ts}`;
      resetForNewRun(run_name);
      useStore.getState().setActiveCell({ kind: "sequence", name: run_name });
      useStore.getState().setSimKind("sim");
      await api.runs.start({
        run_name,
        model_path: activeModel!.path,
        recipe_data: baseToSend,
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
    // The error message commonly arrives as `HTTP 422: {"detail":"..."}`.
    // Parse the detail out so we can show it inline — much more useful
    // than a generic "Error" button that hides the cause in a tooltip.
    const detail = extractDetail(error);
    return (
      <div className="inline-flex items-stretch text-xs font-medium">
        <button
          type="button"
          onClick={onRun}
          disabled={busy}
          title={error ?? "Sim errored — click to retry"}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-l-md
                     bg-error/15 border border-error/40 text-error
                     hover:bg-error/20
                     focus:outline-none focus-visible:ring-2 focus-visible:ring-error/40
                     transition-colors duration-fast"
        >
          <X size={11} />
          Retry
        </button>
        {detail && (
          <div
            className="max-w-[420px] truncate px-3 py-1.5 rounded-r-md
                       bg-error/10 border border-l-0 border-error/40 text-error/90
                       text-[11px] font-normal"
            title={detail}
          >
            {detail}
          </div>
        )}
      </div>
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

/** Pull a human-readable cause out of an error message like
 *  `HTTP 422: {"detail":"..."}`. Falls back to the raw message when the
 *  shape isn't recognized. Returns null for empty input. */
function extractDetail(raw: string | null): string | null {
  if (!raw) return null;
  // Try to find a JSON body after `HTTP NNN: `
  const bodyIdx = raw.indexOf(": ");
  if (bodyIdx >= 0) {
    const body = raw.slice(bodyIdx + 2).trim();
    if (body.startsWith("{")) {
      try {
        const parsed = JSON.parse(body);
        if (typeof parsed?.detail === "string") return parsed.detail;
        if (Array.isArray(parsed?.detail)) {
          // FastAPI request-validation errors are an array of issues
          return parsed.detail.map((d: { msg?: string; loc?: unknown[] }) =>
            d.msg ? `${(d.loc ?? []).join(".")}: ${d.msg}` : JSON.stringify(d),
          ).join("; ");
        }
      } catch { /* not JSON, fall through */ }
    }
  }
  return raw;
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
