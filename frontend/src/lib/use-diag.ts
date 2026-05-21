/** Diagnostics hook for the StatusPill.
 *
 * Polls two independent endpoints — backend and viser_headless — every
 * `refetchInterval` ms, and folds them into a single DiagSnapshot the
 * pill can render without further logic.
 *
 * sync_daemon used to be a third leg of this hook (back when the SPA
 * ran on the client and downloaded npz cells from the server). The
 * server-only deployment doesn't have a sync daemon — everything is
 * on one host — so that leg was removed.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { DiagSnapshot, DiagPart, ViserState } from "./types";

// Production builds inject VITE_VISER_CONTROL_URL at compile time
// (./.env.production sets it to "/viser-ctrl" — same-origin proxy).
// Dev defaults to the same path so a `vite` proxy rule covers it.
const VISER_CONTROL_URL =
  (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined)?.replace(/\/$/, "")
  ?? "/viser-ctrl";

async function fetchJSON<T>(url: string, signal: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

const STALE: DiagPart = { ok: false, error: "pending" };

export function useDiag(refetchInterval = 5000): DiagSnapshot {
  const backendQ = useQuery({
    queryKey: ["diag", "backend"],
    queryFn:  () => api.diag.health(),
    refetchInterval,
    retry: false,
  });

  const viserQ = useQuery({
    queryKey: ["diag", "viser"],
    queryFn:  ({ signal }) => fetchJSON<ViserState>(`${VISER_CONTROL_URL}/state`, signal!),
    refetchInterval,
    retry: false,
  });

  const backend: DiagPart = backendQ.isError
    ? { ok: false, error: (backendQ.error as Error)?.message ?? "unreachable" }
    : backendQ.data
    ? { ok: true,  detail: backendQ.data.status }
    : STALE;

  const viser: DiagPart & { raw?: ViserState } = viserQ.isError
    ? { ok: false, error: "control API unreachable" }
    : viserQ.data
    ? {
        ok: true,
        detail: `${viserQ.data.cells.length} cells · ${viserQ.data.cell} f${viserQ.data.frame}/${viserQ.data.n_frames}`,
        raw: viserQ.data,
      }
    : STALE;

  return { backend, viser };
}
