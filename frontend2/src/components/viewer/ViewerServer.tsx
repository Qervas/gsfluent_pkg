import { useEffect, useRef, useState } from "react";
import { useRenderSession } from "@/hooks/useRenderSession";
import type { RenderTarget } from "@/lib/webrtc";
import { StatsHud, type ViewerStats } from "./StatsHud";

export function ViewerServer({
  target, enabled,
}: { target: RenderTarget | null; enabled: boolean }): JSX.Element {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const { state, stream, error, client } = useRenderSession(target, enabled);
  const [stats, setStats] = useState<ViewerStats>({ mode: "server" });

  useEffect(() => {
    if (videoRef.current && stream) {
      videoRef.current.srcObject = stream;
    }
  }, [stream]);

  // Pull bitrate / fps / rtt from the peer's stats API once per second.
  useEffect(() => {
    if (!client?.pc || state !== "connected") return;
    let prev: { bytes: number; ts: number } | null = null;
    const id = setInterval(async () => {
      const pc = client.pc;
      if (!pc) return;
      const reports = await pc.getStats();
      let rttMs: number | undefined;
      let fps: number | undefined;
      let bitrateKbps: number | undefined;
      reports.forEach((r) => {
        if (r.type === "inbound-rtp" && (r as { kind?: string }).kind === "video") {
          const bytes = (r as { bytesReceived?: number }).bytesReceived ?? 0;
          fps = (r as { framesPerSecond?: number }).framesPerSecond;
          if (prev) {
            const dt = (r.timestamp - prev.ts) / 1000;
            bitrateKbps = dt > 0 ? ((bytes - prev.bytes) * 8) / dt / 1000 : undefined;
          }
          prev = { bytes, ts: r.timestamp };
        }
        if (r.type === "candidate-pair" && (r as { state?: string }).state === "succeeded") {
          rttMs = ((r as { currentRoundTripTime?: number }).currentRoundTripTime ?? 0) * 1000;
        }
      });
      setStats({ mode: "server", fps, bitrateKbps, rttMs });
    }, 1000);
    return () => clearInterval(id);
  }, [client, state]);

  return (
    <div className="relative w-full aspect-video bg-black rounded border border-border overflow-hidden">
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="w-full h-full object-contain"
      />
      <div className="absolute top-2 left-2 pill bg-slate-900/80 text-slate-300">
        server · {state}
      </div>
      {error && (
        <div className="absolute inset-x-2 top-10 text-xs text-red-300 bg-red-900/40 p-2 rounded">
          {error}
        </div>
      )}
      {!enabled && (
        <div className="absolute inset-0 grid place-items-center text-slate-500 text-sm">
          (viewer paused)
        </div>
      )}
      <StatsHud stats={stats} />
    </div>
  );
}
