/** Diagnostics hook for the StatusPill.
 *
 * Polls three independent endpoints — backend, sync_daemon (via
 * viser_headless's pass-through), and viser_headless itself — every
 * `refetchInterval` ms, and folds them into a single DiagSnapshot the
 * pill can render without further logic.
 *
 * Why three separate queries (rather than one merged endpoint)? Each
 * source is on a different process boundary and one being unreachable
 * shouldn't taint the others. react-query gives independent retry +
 * failure state per query for free; combining at the leaf level
 * preserves that.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type {
  DiagSnapshot,
  DiagPart,
  SyncStatus,
  ViserState,
} from "./types";

// Same fallback as ViserSplatScene / Viewport so the pill agrees with
// the actual viewport about which viser_headless to talk to.
function controlUrl(): string {
  const env = (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined)
    ?.replace(/\/$/, "");
  if (env) return env;
  // SSR-safe: `window` is undefined during build.
  const host = typeof window !== "undefined" ? window.location.hostname : "localhost";
  return `http://${host}:8092`;
}

async function fetchJSON<T>(url: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function ageString(unix?: number): string | undefined {
  if (!unix) return undefined;
  const dt = Math.max(0, Date.now() / 1000 - unix);
  if (dt < 60)    return `${Math.round(dt)}s ago`;
  if (dt < 3600)  return `${Math.round(dt / 60)}m ago`;
  return `${Math.round(dt / 3600)}h ago`;
}

const STALE: DiagPart = { ok: false, error: "pending" };

export function useDiag(refetchInterval = 5000): DiagSnapshot {
  const backendQ = useQuery({
    queryKey: ["diag", "backend"],
    queryFn:  () => api.diag.health(),
    refetchInterval,
    retry: false,
  });

  const syncQ = useQuery({
    queryKey: ["diag", "sync"],
    queryFn:  ({ signal }) => fetchJSON<SyncStatus>(`${controlUrl()}/sync-status`, signal!),
    refetchInterval,
    retry: false,
  });

  const viserQ = useQuery({
    queryKey: ["diag", "viser"],
    queryFn:  ({ signal }) => fetchJSON<ViserState>(`${controlUrl()}/state`, signal!),
    refetchInterval,
    retry: false,
  });

  // ---- backend -------------------------------------------------------
  const backend: DiagPart = backendQ.isError
    ? { ok: false, error: (backendQ.error as Error)?.message ?? "unreachable" }
    : backendQ.data
    ? { ok: true,  detail: backendQ.data.status }
    : STALE;

  // ---- sync daemon ---------------------------------------------------
  //
  // The endpoint itself succeeds even when the daemon is dead (it just
  // returns {online: false, error: ...}). So we look at the payload to
  // decide ok-ness, not the HTTP status.
  let sync: DiagPart & { raw?: SyncStatus };
  if (syncQ.isError) {
    sync = { ok: false, error: "viser /sync-status unreachable" };
  } else if (syncQ.data) {
    const s = syncQ.data;
    sync = s.online
      ? {
          ok: true,
          detail: `last sync ${ageString(s.last_success_unix) ?? "—"} · ${s.sequences_seen ?? 0} sequences`,
          raw: s,
        }
      : { ok: false, error: s.error ?? "offline", raw: s };
  } else {
    sync = STALE;
  }

  // ---- viser ---------------------------------------------------------
  const viser: DiagPart & { raw?: ViserState } = viserQ.isError
    ? { ok: false, error: "control API unreachable" }
    : viserQ.data
    ? {
        ok: true,
        detail: `${viserQ.data.cells.length} cells · ${viserQ.data.cell} f${viserQ.data.frame}/${viserQ.data.n_frames}`,
        raw: viserQ.data,
      }
    : STALE;

  return { backend, sync, viser };
}
