import { useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

// tqdm renders progress as e.g.
//   "  70%|███████   | 105/150 [00:36<00:15,  2.83it/s]"
// The bar chars are unicode block elements (U+2588 family). We don't
// care about them — just want the (n, total) pair before "[".
const TQDM_RE = /^\s*\d+%\|[^|]*\|\s*(\d+)\/(\d+)\s+\[/;

// Wrapper-script + runner emit explicit done/error markers. Detecting
// either flips simState so the workbench shows "Done" instead of
// "Cancel 0%" after the run finishes. Order matters: error markers
// checked first so a trailing "done" log from the runner doesn't
// over-write an earlier error transition.
const ERROR_MARKERS = [
  /^Traceback \(most recent call last\):/,
  /^ERROR: /,
  /^\[runner\] .*FAILED/,
];
const DONE_MARKERS = [
  /^=== run_sim\.sh done:/,
  /^\[runner\] \.npz cache built for /,
];

/**
 * Tails the backend's run.log into the workbench console.
 *
 * Polls `GET /api/runs/<name>/log?offset=<n>` every `pollMs` (default
 * 500 ms) while a sim is active. Splits incoming bytes by newline and
 * pushes each line through `appendLog` so the StatusPanel console
 * surfaces stdout/stderr live.
 *
 * Why polling, not SSE: gsfluent's run.log is a flat append-only file
 * the runner already writes line-buffered; reading from an offset is
 * trivial. SSE would require keeping a long-lived response alive
 * through the SSH tunnel and we already burn one tick per second on
 * /api/runs for the active-list anyway. Polling matches the existing
 * cadence and is easier to reason about under reconnects.
 *
 * Activation rules:
 *   - Only when an active sim is detected: simState==='running' AND
 *     activeCell is a sequence. The active sequence's name is the
 *     run_name in the URL.
 *   - When the sim finishes/errors/cancels, we do one final poll to
 *     drain any tail lines the runner wrote between the last tick and
 *     the state transition.
 *   - Resetting for a new run clears simLog (resetForNewRun in the
 *     store), so we re-anchor offset to 0 whenever the run name flips.
 */
export function useRunLogPoller(pollMs: number = 500): void {
  const simState = useStore((s) => s.simState);
  const activeCell = useStore((s) => s.activeCell);
  const appendLog = useStore((s) => s.appendLog);
  const setSimProgress = useStore((s) => s.setSimProgress);
  const setSimState = useStore((s) => s.setSimState);

  // Per-run offset cursor. Resets when the run name changes.
  const offsetRef = useRef<number>(0);
  const runNameRef = useRef<string | null>(null);
  // Carry-over for a chunk that didn't end on a newline — avoids
  // emitting a half-line that the next chunk will complete.
  const partialRef = useRef<string>("");

  const runName =
    activeCell?.kind === "sequence" ? activeCell.name : null;
  const active = simState === "running" && runName !== null;
  // Allow one tail-drain poll after the state flips off "running".
  const shouldDrain = !active && runName !== null && runName === runNameRef.current;

  useEffect(() => {
    // Skip-or-fire decision: nothing to do if we're not active and
    // there's no final-drain to perform.
    if (!active && !shouldDrain) return;

    // Reset cursor synchronously WITHIN this effect when the run name
    // has changed. A separate effect on [runName] would race with this
    // one — React's commit order isn't guaranteed across effects, and
    // an earlier version of this code could send the previous run's
    // partial line as the first tick of a new run. Doing it here means
    // "reset and start polling" are one transactional step.
    if (runName !== runNameRef.current) {
      runNameRef.current = runName;
      offsetRef.current = 0;
      partialRef.current = "";
    }

    let cancelled = false;

    const tick = async () => {
      if (cancelled || runName === null) return;
      try {
        const r = await api.runs.log(runName, offsetRef.current);
        if (cancelled) return;
        if (r.content.length > 0) {
          // Prepend any saved partial line from the previous chunk; the
          // newline split below re-creates whole lines.
          const text = partialRef.current + r.content;
          const lines = text.split("\n");
          // The last entry is everything after the final \n. If the
          // chunk didn't end on \n, it's an incomplete line — hold it.
          partialRef.current = lines.pop() ?? "";
          // tqdm uses carriage returns to redraw the same line in place,
          // so an emitted chunk often contains many \r-separated frames
          // of the same bar. We split on \r too so the parser sees every
          // progress update — otherwise we'd only see the final tick in
          // each batch and the percentage would jump in big steps.
          for (const ln of lines) {
            if (ln.length === 0) continue;
            const subLines = ln.split("\r");
            for (const sub of subLines) {
              const s = sub.trim();
              if (s.length === 0) continue;
              appendLog(s);
              // Progress parse: tqdm "n/total".
              const m = s.match(TQDM_RE);
              if (m) {
                const n = parseInt(m[1], 10);
                const total = parseInt(m[2], 10);
                if (Number.isFinite(n) && Number.isFinite(total) && total > 0) {
                  setSimProgress(n, total);
                }
                continue;  // a tqdm line is never a done/error marker
              }
              // Terminal-state transitions. Check errors first so a
              // trailing "done" line doesn't paper over a failure.
              if (ERROR_MARKERS.some((re) => re.test(s))) {
                setSimState("error");
              } else if (DONE_MARKERS.some((re) => re.test(s))) {
                setSimState("done");
                // On a clean finish, lock the progress at 100%.
                const st = useStore.getState();
                if (st.simTotalFrames > 0) {
                  setSimProgress(st.simTotalFrames, st.simTotalFrames);
                }
              }
            }
          }
        }
        offsetRef.current = r.offset;
      } catch (err) {
        /* For library sequences (not active runs), /api/runs/{name}/log is
           a hard 404 that will never resolve. Permanent-404 → stop polling.
           Transient errors (network blip, brief 5xx) → keep polling. */
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.startsWith("HTTP 404")) {
          cancelled = true;
        }
      }
    };

    // First tick immediately so the console doesn't sit empty for the
    // first 500 ms; then settle into the regular cadence.
    void tick();

    if (shouldDrain) {
      // One-shot drain — no interval. The next time `active` flips
      // back on, the run-name effect above will reset the cursor.
      return () => { cancelled = true; };
    }

    const id = setInterval(() => void tick(), pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active, shouldDrain, runName, pollMs, appendLog]);
}
