import { useEffect } from "react";
import { type EventMsg, streamClient } from "@/lib/ws";

export function useStream(channels: string[], onEvent: (e: EventMsg) => void): void {
  useEffect(() => {
    const unsubs = channels.map((c) => streamClient.subscribe(c, onEvent));
    return () => {
      for (const u of unsubs) u();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channels.join("|")]);
}
