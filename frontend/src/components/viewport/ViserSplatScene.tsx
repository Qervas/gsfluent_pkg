import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";
import { CellRef } from "@/lib/cell";

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
  // In-flight cell resolution on viser's side (model fetch / mmap).
  // Mirrored from /state.loading so the SPA can show phase progress
  // while /set is blocked waiting for resolve_cell_lazily.
  const [loading, setLoading] = useState<{
    name: string; phase: string; error: string | null;
  } | null>(null);

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
        setLoading(d.loading ?? null);
        setViserState({
          cell: d.cell ?? null,
          frame: d.frame ?? 0,
          n_frames: d.n_frames ?? 0,
          // pushed_frame is the actually-rendered cursor (no-skip
          // invariant). The bar reads this so the displayed index
          // never leads the splats. -1 means "no frame pushed yet".
          pushed_frame: typeof d.pushed_frame === "number" ? d.pushed_frame : -1,
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
            const body = (await r.json()) as {
              ok?: boolean; cell?: string; frame?: number; pushed_frame?: number
            };
            if (body?.ok && typeof body.frame === "number") {
              const s = useStore.getState().viserState;
              const nextPushed =
                typeof body.pushed_frame === "number" ? body.pushed_frame : s.pushed_frame;
              if (body.frame !== s.frame || nextPushed !== s.pushed_frame) {
                useStore.getState().setViserState({
                  ...s,
                  frame: body.frame,
                  pushed_frame: nextPushed,
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
  // .npz has landed yet — just needs to wait.
  const simState = useStore((s) => s.simState);
  const cellRef = CellRef.tryParseWire(cellName);
  const simIsRunning =
    simState === "running" && cellRef?.kind === "sequence";

  // ── on-demand cache build ──────────────────────────────────────────────
  // When the selected cell is a sequence with no local .npz, instead of
  // telling the user to ssh in and run a Python script (the old UX),
  // expose a button that drives the full build → sync → reload flow:
  //
  //   1. POST /api/sequences/{name}/cache/build    (server-side build)
  //   2. poll /api/sequences/{name}/cache/build-status until done
  //   3. POST {viser}/sync_cell?name=…&url=…       (download + mmap)
  //
  // sync_cell hits the backend through the vite proxy when the SPA was
  // built without VITE_BACKEND_URL (the default — local dev / preview),
  // and direct otherwise.
  const seqName = cellRef?.kind === "sequence" ? cellRef.name : null;
  type BuildState = "idle" | "building" | "syncing" | "error";
  const [buildState, setBuildState] = useState<BuildState>("idle");
  const [buildError, setBuildError] = useState<string | null>(null);

  async function startBuild(name: string) {
    const apiBase = (import.meta.env.VITE_BACKEND_URL as string | undefined)?.replace(/\/$/, "") ?? "";
    const buildBase = apiBase || ""; // empty → relative, proxied by vite
    setBuildError(null);
    setBuildState("building");
    try {
      const r1 = await fetch(`${buildBase}/api/sequences/${encodeURIComponent(name)}/cache/build`, { method: "POST" });
      if (!r1.ok) throw new Error(`build kickoff failed: HTTP ${r1.status}`);

      // Poll for completion. 2s tick is fine for builds in the 30-90s range.
      while (true) {
        await new Promise((res) => setTimeout(res, 2000));
        const r2 = await fetch(`${buildBase}/api/sequences/${encodeURIComponent(name)}/cache/build-status`);
        if (!r2.ok) throw new Error(`status poll failed: HTTP ${r2.status}`);
        const s = await r2.json();
        if (s.state === "done") break;
        if (s.state === "error") throw new Error(s.error || "build failed");
        // state "building" / "idle" — keep polling.
      }

      // Download the .gsq directly. The npz format is retired — the
      // build flow on the server only produces .gsq, and viser_headless
      // only decodes .gsq.
      const origin = apiBase || window.location.origin;
      const seqEnc = encodeURIComponent(name);
      const downloadUrl = `${origin}/api/sequences/${seqEnc}/cache/splats.gsq`;
      setBuildState("syncing");
      const r3 = await fetch(
        `${controlUrl}/sync_cell?name=${seqEnc}&url=${encodeURIComponent(downloadUrl)}`,
        { method: "POST" },
      );
      const d3 = await r3.json();
      if (!d3.ok) throw new Error(d3.error || "sync_cell failed");
      // Success: cellMissing will flip false on the next /state poll
      // (the 500ms tick above), so we don't need to force-refresh here.
      setBuildState("idle");
    } catch (e: unknown) {
      setBuildError(e instanceof Error ? e.message : String(e));
      setBuildState("error");
    }
  }

  // When viser reports a sequence-cell as not_found, that means the .npz
  // isn't in this client's local cache yet. The fix is the Build flow
  // (server-side cache build + client-side download via /sync_cell). Auto-
  // trigger it here so the user doesn't have to click — they'll see the
  // buildState progress pill take over from the viser error.
  useEffect(() => {
    if (
      loading?.phase === "error" &&
      loading?.error === "not_found" &&
      loading.name.startsWith("sequence:") &&
      buildState === "idle"
    ) {
      const seq = loading.name.slice("sequence:".length);
      startBuild(seq);
    }
    // startBuild is defined per-render but is a stable closure over the
    // refs it touches; pulling it into deps would make this fire on every
    // poll. Intentionally omitted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading?.name, loading?.phase, loading?.error, buildState]);

  // Labels for the upper-left progress / error pill. Pulled out so the
  // mapping is editable in one spot without touching the rendering JSX.
  // The viser /state endpoint returns wire-format names; parse defensively
  // so a malformed payload falls back to the raw string rather than
  // throwing inside render.
  function progressLabel(phase: string, name: string): string | null {
    const bare = CellRef.tryParseWire(name)?.name ?? name;
    if (phase === "fetching") return `Fetching ${bare}…`;
    if (phase === "parsing")  return "Parsing splats…";
    return null;
  }
  function errorLabel(tag: string | null, name: string): string {
    const kind: "model" | "sequence" =
      CellRef.tryParseWire(name)?.kind ?? "model";
    if (tag === "not_found")    return `${kind === "model" ? "Model" : "Sequence"} not found on backend.`;
    if (tag === "fetch_failed") return `Couldn't fetch ${kind} from backend.`;
    if (tag === "parse_failed") return `${kind === "model" ? "Model" : "Sequence"} loaded but couldn't be parsed.`;
    return "Load failed.";
  }

  // ── unified progress pill: pick which source to surface ──────────────────
  // buildState (SPA-side build flow) wins over viser's loading state when
  // active, because the build flow is the recovery path for the most
  // common viser error (sequence not_found). Renders ONE pill, not two.
  type Pill = { tone: "info" | "error"; text: string } | null;
  const buildLabel: Record<BuildState, string | null> = {
    idle: null,
    building: "Building cache on server…",
    syncing: "Downloading cache locally…",
    error: buildError ?? "Build failed.",
  };
  const buildPill: Pill = buildState === "idle"
    ? null
    : buildState === "error"
      ? { tone: "error", text: buildError ?? "Build failed." }
      : { tone: "info", text: buildLabel[buildState]! };
  const loadingIsAutoRecovering =
    loading?.phase === "error" &&
    loading?.error === "not_found" &&
    CellRef.tryParseWire(loading?.name)?.kind === "sequence";
  const viserPill: Pill = !loading || loadingIsAutoRecovering
    ? null
    : loading.phase === "error"
      ? { tone: "error", text: errorLabel(loading.error, loading.name) }
      : (progressLabel(loading.phase, loading.name)
          ? { tone: "info", text: progressLabel(loading.phase, loading.name)! }
          : null);
  const pill: Pill = buildPill ?? viserPill;

  return (
    <div className="relative h-full w-full bg-canvas">
      <iframe
        src={viserUrl}
        title="viser splat viewer"
        style={{
          width: "100%",
          height: "100%",
          border: "none",
          background: "#0a0f1a",
          display: "block",
        }}
        sandbox="allow-scripts allow-same-origin allow-pointer-lock"
        allowFullScreen
      />
      {pill && pill.tone === "info" && (
        <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/90 border border-border text-text-primary text-xs rounded backdrop-blur flex items-center gap-2 z-10">
          <Loader2 size={12} className="animate-spin text-accent" />
          <span>{pill.text}</span>
        </div>
      )}
      {pill && pill.tone === "error" && (
        <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/90 border border-warning text-warning text-xs rounded backdrop-blur z-10 flex items-center gap-3">
          <span>{pill.text}</span>
          {buildState === "error" && seqName && (
            <button
              type="button"
              className="px-2 py-0.5 rounded bg-elevated text-text-primary hover:opacity-90 text-xs border border-border"
              onClick={() => startBuild(seqName)}
            >
              Retry
            </button>
          )}
        </div>
      )}
      {cellMissing && !loading && !pill && (
        simIsRunning ? (
          <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/85 border border-border text-text-muted text-xs rounded flex items-center gap-2 backdrop-blur">
            <Loader2 size={12} className="animate-spin text-accent" />
            <span>Waiting for first frame from sim…</span>
          </div>
        ) : seqName ? (
          <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/90 border border-border text-text-primary text-xs rounded backdrop-blur flex items-center gap-3 max-w-[28rem]">
            {buildState === "idle" && (
              <>
                <span>Sequence cache not on this client.</span>
                <button
                  type="button"
                  className="px-2 py-0.5 rounded bg-accent text-canvas hover:opacity-90 text-xs"
                  onClick={() => startBuild(seqName)}
                >
                  Build
                </button>
              </>
            )}
            {buildState === "building" && (
              <>
                <Loader2 size={12} className="animate-spin text-accent" />
                <span>Building cache on server…</span>
              </>
            )}
            {buildState === "syncing" && (
              <>
                <Loader2 size={12} className="animate-spin text-accent" />
                <span>Downloading cache locally…</span>
              </>
            )}
            {buildState === "error" && (
              <>
                <span className="text-warning">{buildError ?? "build failed"}</span>
                <button
                  type="button"
                  className="px-2 py-0.5 rounded bg-elevated text-text-primary hover:opacity-90 text-xs border border-border"
                  onClick={() => startBuild(seqName)}
                >
                  Retry
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="absolute top-[68px] left-3 px-3 py-2 bg-elevated/90 border border-warning text-warning text-xs rounded backdrop-blur">
            Cell <code>{cellName}</code> not loaded.
          </div>
        )
      )}
    </div>
  );
}
