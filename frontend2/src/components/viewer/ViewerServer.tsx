import { useEffect, useRef } from "react";
import { useRenderSession } from "@/hooks/useRenderSession";
import type { RenderTarget } from "@/lib/webrtc";

export function ViewerServer({
  target, enabled,
}: { target: RenderTarget | null; enabled: boolean }): JSX.Element {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const { state, stream, error } = useRenderSession(target, enabled);

  useEffect(() => {
    if (videoRef.current && stream) {
      videoRef.current.srcObject = stream;
    }
  }, [stream]);

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
    </div>
  );
}
