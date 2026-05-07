import { useEffect, useCallback } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Outliner } from "@/components/outliner/Outliner";
import { Properties } from "@/components/properties/Properties";
import { Viewport } from "@/components/viewport/Viewport";
import { useStreamClient } from "@/lib/use-stream";
import { useStore } from "@/lib/store";

export default function App() {
  const client = useStreamClient();
  const resetForNewRun = useStore((s) => s.resetForNewRun);

  useEffect(() => {
    client.connect();
  }, [client]);

  // For RunButton: just hook the WS to the new run; state reset already happened.
  const subscribe = useCallback(
    (run_name: string) => {
      client.subscribe(run_name);
    },
    [client],
  );

  // For Outliner's HistoryTree: fully reset + subscribe.
  const onLoadRun = useCallback(
    (run_name: string) => {
      resetForNewRun(run_name);
      client.subscribe(run_name);
    },
    [client, resetForNewRun],
  );

  return (
    <AppShell
      subscribe={subscribe}
      outliner={<Outliner onLoadRun={onLoadRun} />}
      viewport={<Viewport />}
      properties={<Properties />}
    />
  );
}
