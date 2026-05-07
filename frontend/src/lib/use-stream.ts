import { useMemo } from "react";
import { StreamClient } from "./ws";
import { useStore } from "./store";

export function useStreamClient(): StreamClient {
  const setSimState     = useStore((s) => s.setSimState);
  const appendLog       = useStore((s) => s.appendLog);
  const setStaticAttrs  = useStore((s) => s.setStaticAttrs);
  const putFrame        = useStore((s) => s.putFrame);

  return useMemo(
    () =>
      new StreamClient({
        onStatus: (m) => {
          // Backend may send any string; coerce to known SimState shape.
          const s = m.state as
            | "idle"
            | "running"
            | "done"
            | "error"
            | "cancelled";
          setSimState(s);
        },
        onLog: (m) => appendLog(m.line),
        onStaticAttrs: (m) => setStaticAttrs(m.attrs),
        onFrame: (meta, xyz) => putFrame(meta.frame_idx, xyz),
        onError: (m) => appendLog(`[error:${m.code}] ${m.run_name}: ${m.message}`),
      }),
    [setSimState, appendLog, setStaticAttrs, putFrame],
  );
}
