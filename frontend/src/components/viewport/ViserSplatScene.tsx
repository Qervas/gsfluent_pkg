import { useEffect, useRef, useState } from "react";
import { useStore } from "@/lib/store";

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
 * All controls live in the React UI: outliner picks `simRunName`, PlaybackBar
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

  // Pull what we need to forward to viser. simRunName names the active
  // sequence ("jelly_cluster_server" etc.); the viser .npz cache uses the
  // same stem so we send the bare run name as the cell.
  const simRunName = useStore((s) => s.simRunName);
  const currentFrameIdx = useStore((s) => s.currentFrameIdx);

  // Track what we've already sent so we don't re-POST identical state on
  // every render tick. The set/state on the server already does this
  // implicitly, but skipping noop requests keeps DevTools network panel
  // readable when debugging.
  const lastSent = useRef<{ cell: string | null; frame: number | null }>({
    cell: null,
    frame: null,
  });

  // Sidecar control: forward (cell, frame) on change. We also list
  // available cells once on mount to report mismatches cleanly.
  const [serverCells, setServerCells] = useState<string[] | null>(null);
  const [controlReachable, setControlReachable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${controlUrl}/state`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        setServerCells(data.cells ?? []);
        setControlReachable(true);
      })
      .catch(() => {
        if (!cancelled) setControlReachable(false);
      });
    return () => { cancelled = true; };
  }, [controlUrl]);

  // Forward state changes. Only POST if (cell, frame) actually changed.
  // simRunName === "_model:foo" is a static-model preview, not a sequence;
  // pass it through anyway so the cell-mismatch path in the API reports a
  // clear "unknown cell" error if it really doesn't exist.
  useEffect(() => {
    if (!controlReachable) return;
    const cell = simRunName ?? null;
    const frame = currentFrameIdx;
    if (lastSent.current.cell === cell && lastSent.current.frame === frame) {
      return;
    }
    lastSent.current = { cell, frame };
    const payload: { cell?: string; frame?: number } = { frame };
    if (cell) payload.cell = cell;
    fetch(`${controlUrl}/set`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => {
      /* network blip — next state change will retry; no need to surface */
    });
  }, [controlReachable, controlUrl, simRunName, currentFrameIdx]);

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
{`python tools/viser_headless.py \\
  --npz_dir work/cache/viser \\
  --viser_port 8091 --control_port 8092`}
          </pre>
          <div className="text-xs mt-3">
            If the cache is empty: <code>python tools/batch_convert_to_npz.py</code> first.
          </div>
        </div>
      </div>
    );
  }

  const cellMissing =
    serverCells !== null && simRunName !== null && !simRunName.startsWith("_model:") &&
    !serverCells.includes(simRunName);

  return (
    <div className="relative h-full w-full bg-canvas">
      <iframe
        src={viserUrl}
        title="viser splat viewer"
        style={{
          width: "100%",
          height: "100%",
          border: "none",
          background: "#0d1117",
          display: "block",
        }}
        sandbox="allow-scripts allow-same-origin allow-pointer-lock allow-fullscreen"
      />
      {cellMissing && (
        <div className="absolute top-2 left-2 px-3 py-2 bg-elevated/90 border border-warning text-warning text-xs rounded">
          Sequence <code>{simRunName}</code> not in viser cache. Run{" "}
          <code>python tools/batch_convert_to_npz.py {simRunName}</code>.
        </div>
      )}
    </div>
  );
}
