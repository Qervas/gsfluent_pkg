import { useEffect, useRef } from "react";
import { useStore } from "@/lib/store";

/**
 * Watches sync_daemon's per-sequence download progress and emits
 * `[sync] <name>: 86% (2.4 GB / 2.8 GB @ 22 MB/s)` lines into the
 * workbench console so the user sees that downloads are happening.
 *
 * Backend cadence: sync_daemon flushes the status JSON on each chunk
 * heartbeat (~2 s or ~16 MB). We poll viser_headless's /sync-status
 * (which serves that JSON verbatim) every 2 s and emit a line only on
 * change — no chatter when nothing's moving.
 *
 * Why /sync-status (viser_headless) and not the status file directly:
 * the SPA can't read /run/user/<uid>/gsfluent_sync_status.json from
 * the browser sandbox. viser_headless already proxies it through HTTP
 * for the diagnostics pill; we piggyback on the same endpoint.
 *
 * Completion: when a sequence's `viser_npz.ok === true` appears in the
 * status (the daemon's "done" signal), we emit one final 100% line and
 * stop tracking that sequence so a re-rendered tick doesn't double-emit.
 */
type DownloadEntry = { bytes: number; total: number | null; updated_unix: number };
type PerSeqEntry = {
  download?: DownloadEntry;
  viser_npz?: { ok: boolean; bytes?: number; synced_unix?: number };
};
type SyncStatus = {
  per_sequence: Record<string, PerSeqEntry>;
};

function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)} kB`;
  return `${n} B`;
}

export function useSyncProgressPoller(pollMs: number = 2000): void {
  const appendLog = useStore((s) => s.appendLog);

  // Per-sequence: last bytes we logged (so a heartbeat that doesn't
  // move bytes silently is dropped) and last timestamp (so we can
  // compute MB/s between emits).
  const lastBytesRef = useRef<Record<string, number>>({});
  const lastTimeRef = useRef<Record<string, number>>({});
  // Sequences we've already announced as complete — never re-announce.
  const doneRef = useRef<Set<string>>(new Set());

  const controlUrl =
    (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined) ||
    `http://${typeof location !== "undefined" ? location.hostname : "localhost"}:8092`;

  useEffect(() => {
    let cancelled = false;

    const tick = async () => {
      if (cancelled) return;
      try {
        const r = await fetch(`${controlUrl}/sync-status`);
        if (!r.ok) return;
        const s = (await r.json()) as SyncStatus;
        if (cancelled) return;
        const per = s.per_sequence ?? {};
        const now = Date.now() / 1000;
        for (const [name, entry] of Object.entries(per)) {
          // Completion: viser_npz.ok === true + synced_unix non-zero.
          // The synced_unix check avoids re-announcing stale completions
          // from previous sessions (they all carry synced_unix:0 in
          // the daemon's bootstrap snapshot).
          if (
            entry.viser_npz?.ok &&
            (entry.viser_npz.synced_unix ?? 0) > 0 &&
            !doneRef.current.has(name)
          ) {
            doneRef.current.add(name);
            const totalStr = entry.viser_npz.bytes
              ? ` (${fmtBytes(entry.viser_npz.bytes)})`
              : "";
            appendLog(`[sync] ${name}.npz: download complete${totalStr}`);
            // Clear the in-flight trackers for cleanliness — if the
            // daemon re-downloads this file later it'll start fresh.
            delete lastBytesRef.current[name];
            delete lastTimeRef.current[name];
            continue;
          }
          const dl = entry.download;
          if (!dl) continue;
          const prevBytes = lastBytesRef.current[name];
          if (prevBytes === dl.bytes) continue;  // no movement, skip
          const prevTime = lastTimeRef.current[name];
          let rateStr = "";
          if (prevBytes !== undefined && prevTime !== undefined) {
            const dt = now - prevTime;
            if (dt > 0.1) {
              const mbps = ((dl.bytes - prevBytes) / 1e6) / dt;
              rateStr = ` @ ${mbps.toFixed(1)} MB/s`;
            }
          }
          lastBytesRef.current[name] = dl.bytes;
          lastTimeRef.current[name] = now;
          if (dl.total && dl.total > 0) {
            const pct = Math.min(100, (100 * dl.bytes) / dl.total).toFixed(0);
            appendLog(
              `[sync] ${name}.npz: ${pct}% ` +
              `(${fmtBytes(dl.bytes)} / ${fmtBytes(dl.total)}${rateStr})`,
            );
          } else {
            appendLog(`[sync] ${name}.npz: ${fmtBytes(dl.bytes)}${rateStr}`);
          }
        }
      } catch {
        /* viser_headless unreachable — next tick will retry */
      }
    };

    void tick();
    const id = setInterval(() => void tick(), pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [controlUrl, pollMs, appendLog]);
}
