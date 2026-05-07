import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { StreamClient } from "./ws";
import { useStore } from "./store";
import { api } from "./api";

const AUTO_FINISH_MARKER = "[PhaseA-SUMMARY]";

export function useStreamClient(): StreamClient {
  const setSimState     = useStore((s) => s.setSimState);
  const appendLog       = useStore((s) => s.appendLog);
  const setStaticAttrs  = useStore((s) => s.setStaticAttrs);
  const putFrame        = useStore((s) => s.putFrame);
  const qc = useQueryClient();

  return useMemo(
    () =>
      new StreamClient({
        onStatus: (m) => {
          const s = m.state as
            | "idle" | "running" | "done" | "error" | "cancelled";
          setSimState(s);
          // History reload when sim transitions to a terminal state.
          if (s === "done" || s === "error" || s === "cancelled") {
            qc.invalidateQueries({ queryKey: ["history"] });
          }
        },
        onLog: (m) => {
          appendLog(m.line);
        },
        onStaticAttrs: (m) => setStaticAttrs(m.attrs),
        onFrame: (meta, xyz) => {
          putFrame(meta.frame_idx, xyz);
          // Auto-finish: once the sim has emitted PhaseA-SUMMARY AND we have
          // all expected frames, send a cancel to skip the 10-min fuse-drain.
          // Reading the latest store state via getState() avoids stale closures.
          const st = useStore.getState();
          if (
            st.simState === "running" &&
            st.simRunName &&
            st.simNFrames >= st.simTotalFrames &&
            st.simLog.slice(-80).some((line) => line.includes(AUTO_FINISH_MARKER))
          ) {
            // Best-effort: list runs, find ours, cancel it.
            api.runs.list()
              .then((rs) => {
                const r = rs.find((x) => x.name === st.simRunName);
                if (r) return api.runs.cancel(r.id);
              })
              .catch((e) => console.error("auto-finish cancel failed:", e));
          }
        },
        onError: (m) => appendLog(`[error:${m.code}] ${m.run_name}: ${m.message}`),
      }),
    [setSimState, appendLog, setStaticAttrs, putFrame, qc],
  );
}
