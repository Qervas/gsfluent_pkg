/** Diagnostics hook for the StatusPill.
 *
 * Polls the backend health endpoint every `refetchInterval` ms and
 * folds the result into a DiagSnapshot the pill can render without
 * further logic.
 *
 * Only the backend health leg remains: the SPA renders .gsq sequences
 * in-browser via Spark, and the sync daemon was dropped when the stack
 * moved to a single host.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { DiagSnapshot, DiagPart } from "./types";

const STALE: DiagPart = { ok: false, error: "pending" };

export function useDiag(refetchInterval = 5000): DiagSnapshot {
  const backendQ = useQuery({
    queryKey: ["diag", "backend"],
    queryFn:  () => api.diag.health(),
    refetchInterval,
    retry: false,
  });

  const backend: DiagPart = backendQ.isError
    ? { ok: false, error: (backendQ.error as Error)?.message ?? "unreachable" }
    : backendQ.data
    ? { ok: true,  detail: backendQ.data.status }
    : STALE;

  return { backend };
}
