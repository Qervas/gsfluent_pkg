import { useEffect } from "react";
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

  const onLoadRun = (run_name: string) => {
    resetForNewRun(run_name);
    client.subscribe(run_name);
  };

  return (
    <AppShell
      outliner={<Outliner onLoadRun={onLoadRun} />}
      viewport={<Viewport />}
      properties={<Properties />}
    />
  );
}
