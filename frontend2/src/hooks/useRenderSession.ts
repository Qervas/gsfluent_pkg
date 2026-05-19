import { useEffect, useRef, useState } from "react";
import { RenderSessionClient, type RenderTarget } from "@/lib/webrtc";

type State = "idle" | "connecting" | "connected" | "failed" | "closed";

export function useRenderSession(target: RenderTarget | null, enabled: boolean) {
  const clientRef = useRef<RenderSessionClient | null>(null);
  const [state, setState] = useState<State>("idle");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !target) return;
    let aborted = false;

    const client = new RenderSessionClient();
    clientRef.current = client;

    client.setHandlers({
      onTrack: (track) => {
        if (aborted) return;
        const ms = new MediaStream([track]);
        setStream(ms);
      },
      onState: (s) => {
        if (aborted) return;
        setState(
          s === "connected" ? "connected" :
          s === "failed" ? "failed" :
          s === "closed" ? "closed" :
          "connecting",
        );
      },
    });

    setState("connecting");
    client.connect(target).catch((e) => {
      if (!aborted) {
        setError(String(e));
        setState("failed");
      }
    });

    return () => {
      aborted = true;
      void client.close();
      clientRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(target), enabled]);

  return { state, stream, error, client: clientRef.current };
}
