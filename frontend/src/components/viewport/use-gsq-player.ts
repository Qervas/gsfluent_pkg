import { useEffect, useRef, useState } from "react";
import type { GsqStatic, GsqFrame, WorkerResponse } from "@/lib/gsq";

export type PlayerStatus = "idle" | "loading" | "ready" | "error";

export interface GsqPlayer {
  status: PlayerStatus;
  progress: number;      // bytes received
  total: number | null;  // bytes total (Content-Length) or null
  static: GsqStatic | null;
  error: string | null;
  requestFrame: (idx: number) => void;
}

/** Drives the Stage 1 decode worker for one sequence URL. `onFrame(idx, frame)`
 *  fires when a requested frame is decoded. The worker is recreated whenever
 *  `url` changes and terminated on unmount / url change. */
export function useGsqPlayer(
  url: string | null,
  onFrame: (idx: number, frame: GsqFrame) => void,
  opts?: { createWorker?: () => Worker },
): GsqPlayer {
  const [status, setStatus] = useState<PlayerStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const [stat, setStat] = useState<GsqStatic | null>(null);
  const [error, setError] = useState<string | null>(null);

  const workerRef = useRef<Worker | null>(null);
  const onFrameRef = useRef(onFrame);
  onFrameRef.current = onFrame;

  useEffect(() => {
    if (!url) { setStatus("idle"); return; }
    const worker = opts?.createWorker
      ? opts.createWorker()
      : new Worker(new URL("../../lib/gsq/worker.ts", import.meta.url), { type: "module" });
    workerRef.current = worker;
    setStatus("loading");
    setProgress(0);
    setTotal(null);
    setStat(null);
    setError(null);

    worker.onmessage = (e: MessageEvent<WorkerResponse>) => {
      const m = e.data;
      switch (m.type) {
        case "progress": setProgress(m.received); setTotal(m.total); break;
        case "ready": setStat(m.static); setStatus("ready"); break;
        case "frame": onFrameRef.current(m.idx, { positions: m.positions, quats: m.quats }); break;
        case "error": setError(m.message); setStatus("error"); break;
      }
    };
    worker.postMessage({ type: "open", url });

    return () => { worker.terminate(); workerRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  const requestFrame = (idx: number) => {
    workerRef.current?.postMessage({ type: "frame", idx });
  };

  return { status, progress, total, static: stat, error, requestFrame };
}
