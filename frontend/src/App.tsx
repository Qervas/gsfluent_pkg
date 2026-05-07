import { useEffect, useCallback } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Outliner } from "@/components/outliner/Outliner";
import { Properties } from "@/components/properties/Properties";
import { Viewport } from "@/components/viewport/Viewport";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { useStreamClient } from "@/lib/use-stream";
import { useStore } from "@/lib/store";
import { useShortcuts } from "@/lib/use-shortcuts";
import { api } from "@/lib/api";

export default function App() {
  const client = useStreamClient();
  const resetForNewRun = useStore((s) => s.resetForNewRun);

  useEffect(() => {
    client.connect();
  }, [client]);

  const subscribe = useCallback(
    (run_name: string) => client.subscribe(run_name),
    [client],
  );

  const onLoadRun = useCallback(
    (run_name: string) => {
      resetForNewRun(run_name);
      client.subscribe(run_name);
    },
    [client, resetForNewRun],
  );

  // Run-from-keyboard / palette: replicate the RunButton's flow without owning
  // the busy state — best-effort fire-and-forget.
  const triggerRun = useCallback(async () => {
    const st = useStore.getState();
    if (
      !st.activeModel ||
      !st.activeRecipeName ||
      !st.activeRecipeData ||
      st.simState === "running"
    ) {
      return;
    }
    const ts = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
    const baseName = st.activeRecipeName.replace(/^★ /, "");
    const run_name = `${st.activeModel.name}_${baseName}_${ts}`;
    st.resetForNewRun(run_name);
    try {
      await api.runs.start({
        run_name,
        model_path: st.activeModel.path,
        recipe_data: st.activeRecipeData,
        recipe_source: st.activeRecipeName,
        particles: 200_000,
      });
      client.subscribe(run_name);
    } catch (e) {
      console.error("failed to start run from keyboard:", e);
    }
  }, [client]);

  // Wire the keyboard shortcuts.
  useShortcuts({
    onOpenPalette: () => {
      document.dispatchEvent(new CustomEvent("gsfluent:open-palette"));
    },
    onRun: triggerRun,
    onToggleInspector: () => {
      // Phase 6+ polish — react-resizable-panels' Panel.collapse() requires
      // an imperative ref. Stubbed for now.
      console.log("[shortcut] toggle inspector — not implemented yet");
    },
    onToggleSidebar: () => {
      console.log("[shortcut] toggle sidebar — not implemented yet");
    },
  });

  return (
    <>
      <AppShell
        subscribe={subscribe}
        outliner={<Outliner onLoadRun={onLoadRun} />}
        viewport={<Viewport />}
        properties={<Properties />}
      />
      <CommandPalette onRun={triggerRun} />
    </>
  );
}
