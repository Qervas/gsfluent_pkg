import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";

/**
 * Splats-mode renderer: viser running headless on the server side, React
 * driving it via a sidecar control API.
 *
 * Architecture
 * ────────────
 *   ┌── react workbench ────────────┐    POST /set      ┌─ viser_headless.py ─┐
 *   │  outliner picks sequence ─────┼──→ {cell}     ────┤ FastAPI on :8092    │
 *   │  PlaybackBar advances frame ──┼──→ {frame}    ────┤ updates state {…}   │
 *   │  iframe                       │                   │ pushes to viser ws  │
 *   │  └─ http://localhost:8091  ───┼──── WS ───────────┤ viser on :8091      │
 *   │                               │  (renders splat)  │                     │
 *   └───────────────────────────────┘                   └─────────────────────┘
 *
 * Viser's built-in GUI is gone (viser_headless.py omits `server.gui.*` calls).
 * All controls live in the React UI: outliner picks `activeCell`, PlaybackBar
 * advances `currentFrameIdx`, this component forwards both to viser.
 *
 * URLs are configurable via Vite env vars; defaults match the headless
 * launcher's defaults (`--viser_port 8091 --control_port 8092`):
 *   VITE_VISER_URL          fallback http://<host>:8091/
 *   VITE_VISER_CONTROL_URL  fallback http://<host>:8092
 */
export function ViserSplatScene() {
  const host = location.hostname;
  const viserUrl =
    (import.meta.env.VITE_VISER_URL as string | undefined) ||
    `http://${host}:8091/`;
  const controlUrl =
    (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined) ||
    `http://${host}:8092`;
  // Mixed-content guard. If the SPA is served via https, the browser
  // silently blocks the http://localhost iframe + fetches and the user
  // sees a blank splat viewport with no error. Surface that loudly.
  const mixedContent =
    location.protocol === "https:" &&
    (viserUrl.startsWith("http:") || controlUrl.startsWith("http:"));

  // Pull what we need to forward to viser. activeCell names the active
  // resource — viser's .npz cache key is the cell's wire-format name
  // (kind prefix + bare name), so we use that directly.
  const { wireName } = useActiveCell();
  const currentFrameIdx = useStore((s) => s.currentFrameIdx);

  // Track what we've already sent so we don't re-POST identical state on
  // every render tick. The set/state on the server already does this
  // implicitly, but skipping noop requests keeps DevTools network panel
  // readable when debugging.
  const lastSent = useRef<{ cell: string | null; frame: number | null }>({
    cell: null,
    frame: null,
  });
  // Trailing-edge serialization: at most one /set in flight at a time;
  // newer state replaces whatever's queued. When the in-flight finishes
  // we fire the queued state (if it still differs from what was sent).
  // This drops *intermediate* frames the user can't perceive anyway —
  // viser only renders the latest — while never leaving the bar ahead
  // of the splats (which is what abort-on-stale was doing, since
  // aborted /sets never reached viser).
  const inflight = useRef(false);
  const pending = useRef<{ cell: string | null; frame: number } | null>(null);

  // Sidecar control: forward (cell, frame) on change. We also list
  // available cells once on mount to report mismatches cleanly.
  const [serverCells, setServerCells] = useState<string[] | null>(null);
  const [controlReachable, setControlReachable] = useState<boolean | null>(null);

  const setViserState = useStore((s) => s.setViserState);
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await fetch(`${controlUrl}/state`);
        const d = await r.json();
        if (cancelled) return;
        setControlReachable(true);
        setServerCells(d.cells ?? []);
        setViserState({
          cell: d.cell ?? null,
          frame: d.frame ?? 0,
          n_frames: d.n_frames ?? 0,
        });
      } catch {
        if (!cancelled) setControlReachable(false);
      }
    };
    tick();
    const id = setInterval(tick, 500);
    return () => { cancelled = true; clearInterval(id); };
  }, [controlUrl, setViserState]);

  // Forward state changes. Only POST if (cell, frame) actually changed.
  // `wireName` already carries the `model:` / `sequence:` prefix viser
  // uses to dispatch between .ply (static models) and .npz (sequences).
  //
  // When wireName is null the React side wants the viewport empty, but
  // viser_headless persists its scene state across reconnects — without
  // an explicit clear, an old splat node sticks around and the iframe
  // keeps painting it even though the workbench says "no model loaded".
  // Hit /clear to drop the node.
  useEffect(() => {
    if (!controlReachable) return;
    const cell = wireName;
    const frame = currentFrameIdx;
    if (lastSent.current.cell === cell && lastSent.current.frame === frame) {
      return;
    }
    lastSent.current = { cell, frame };
    if (inflight.current) {
      // A /set is in flight; record the latest desired state and let
      // the in-flight completion handler dispatch it.
      pending.current = { cell, frame };
      return;
    }

    const send = (c: string | null, f: number) => {
      inflight.current = true;
      const url = c === null ? `${controlUrl}/clear` : `${controlUrl}/set`;
      const opts: RequestInit = c === null
        ? { method: "POST" }
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ cell: c, frame: f }),
          };
      fetch(url, opts)
        .then(async (r) => {
          // Echo viser's actual frame back into the store so the bar
          // tracks the splat instead of the React clock. Without this
          // the bar leads the splat whenever the WS push is slower
          // than fpsHint × RTT, because PlaybackDriver advances on a
          // free-running timer but viser only updates per /set.
          if (!r.ok || c === null) return;
          try {
            const body = (await r.json()) as { ok?: boolean; cell?: string; frame?: number };
            if (body?.ok && typeof body.frame === "number") {
              const s = useStore.getState().viserState;
              if (body.frame !== s.frame) {
                useStore.getState().setViserState({
                  ...s,
                  frame: body.frame,
                });
              }
            }
          } catch { /* malformed response — keep going */ }
        })
        .catch(() => {
          /* transient network error — drop, next change will retry */
        })
        .finally(() => {
          inflight.current = false;
          // Flush the latest queued state, if it differs from what we
          // just sent.
          const p = pending.current;
          pending.current = null;
          if (p && (p.cell !== c || p.frame !== f)) {
            send(p.cell, p.frame);
          }
        });
    };
    send(cell, frame);
  }, [controlReachable, controlUrl, wireName, currentFrameIdx]);

  // ---- render ----------------------------------------------------------
  if (mixedContent) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-canvas text-text-muted text-sm">
        <div className="text-center max-w-lg px-6">
          <div className="mb-2 text-text-primary">Splat viewer blocked by browser.</div>
          <div className="text-xs">
            The SPA is served over <code>https</code> but the viser endpoint is
            plain <code>http</code>. Browsers refuse to mix the two.
          </div>
          <div className="text-xs mt-3">
            Either serve viser behind an https reverse proxy and set
            <code> VITE_VISER_URL</code> / <code>VITE_VISER_CONTROL_URL</code>
            accordingly, or load the SPA over plain http for local dev.
          </div>
        </div>
      </div>
    );
  }
  if (controlReachable === false) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-canvas text-text-muted text-sm">
        <div className="text-center max-w-lg px-6">
          <div className="mb-2 text-text-primary">Splat viewer not running.</div>
          <div className="text-xs">Control API not reachable at <code>{controlUrl}</code>.</div>
          <pre className="mt-3 p-2 bg-elevated rounded text-left text-xs whitespace-pre-wrap">
{`python frontend/python/viser_headless.py \\
  --npz_dir work/cache/viser \\
  --viser_port 8091 --control_port 8092`}
          </pre>
          <div className="text-xs mt-3">
            If the cache is empty: <code>python server/tools/batch_convert_to_npz.py</code> first.
          </div>
        </div>
      </div>
    );
  }

  // cellMissing check uses the same wire-format name as the /set forwarder
  // above — viser's `/state.cells` list is in wire format, so this compares
  // apples-to-apples without any prefix-mangling.
  const cellName = wireName;
  const cellMissing =
    serverCells !== null && cellName !== null && !serverCells.includes(cellName);
  // A sim that's mid-flight has the cell selected in the SPA but no
  // .npz has landed yet — the original "run batch_convert_to_npz" copy
  // is wrong here; the user just needs to wait. Distinguish so we can
  // show a friendlier waiting state.
  const simState = useStore((s) => s.simState);
  const simIsRunning =
    simState === "running" &&
    cellName !== null &&
    cellName.startsWith("sequence:");

  return (
    <div className="relative h-full w-full bg-canvas">
      <iframe
        src={viserUrl}
        title="viser splat viewer"
        style={{
          width: "100%",
          height: "100%",
          border: "none",
          // tailwind `canvas` (#0a0f1a) — keep iframe background in
          // lockstep with the R3F Canvas's parent div so toggling
          // modes doesn't flash a different bg color.
          background: "#0a0f1a",
          display: "block",
        }}
        sandbox="allow-scripts allow-same-origin allow-pointer-lock"
        allowFullScreen
      />
      {cellMissing && (
        // top-[68px] parks the banner below the TopBar so the breadcrumb
        // doesn't sit on top of it. The running-sim variant is muted
        // (text-text-muted, no warning border) because waiting is expected.
        simIsRunning ? (
          <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/85 border border-border text-text-muted text-xs rounded flex items-center gap-2 backdrop-blur">
            <Loader2 size={12} className="animate-spin text-accent" />
            <span>Waiting for first frame from sim…</span>
          </div>
        ) : (
          <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/90 border border-warning text-warning text-xs rounded backdrop-blur">
            Cell <code>{cellName}</code> not in viser cache. Run{" "}
            <code>python server/tools/batch_convert_to_npz.py {cellName?.replace(/^sequence:/, "")}</code>.
          </div>
        )
      )}
    </div>
  );
}
