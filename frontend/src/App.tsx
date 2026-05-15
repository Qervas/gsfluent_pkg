import { useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { FullWorkspaceShell } from "@/components/layout/FullWorkspaceShell";
import { Outliner } from "@/components/outliner/Outliner";
import { Properties } from "@/components/properties/Properties";
import { Viewport } from "@/components/viewport/Viewport";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { RecipesWorkspace } from "@/workspaces/RecipesWorkspace";
import { useStreamClient } from "@/lib/use-stream";
import { useStore } from "@/lib/store";
import { api } from "@/lib/api";
import type { SequenceItem } from "@/lib/types";

export default function App() {
  const client = useStreamClient();
  const resetForNewRun = useStore((s) => s.resetForNewRun);
  const activeModel = useStore((s) => s.activeModel);
  const setSimState = useStore((s) => s.setSimState);
  const activeWorkspace = useStore((s) => s.activeWorkspace);
  const simRunName = useStore((s) => s.simRunName);
  const setFpsHint = useStore((s) => s.setFpsHint);

  useEffect(() => {
    client.connect();
  }, [client]);

  // Phase 3: keep fpsHint in sync with the active sequence's _meta.json
  // value. We subscribe to the same cached `["sequences"]` query the
  // SequenceTree uses, so this is essentially free — no extra fetches.
  // Falls back to 24 fps when the active run isn't a known sequence
  // (model preview, mid-load before the list arrives).
  const { data: sequences = [] } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });
  useEffect(() => {
    if (!simRunName) {
      setFpsHint(24);
      return;
    }
    const seq = (sequences as SequenceItem[]).find(
      (s) => s.name === simRunName,
    );
    setFpsHint(seq?.fps_hint ?? 24);
  }, [simRunName, sequences, setFpsHint]);

  // When the user picks a model in the Outliner, render its static ply
  // as a single-frame snapshot. The run-status UI shouldn't claim a sim
  // is in progress just because we're previewing a model, so flip
  // simState back to "idle" right after resetForNewRun.
  useEffect(() => {
    if (!activeModel?.path) return;
    resetForNewRun(`_model:${activeModel.name}`);
    setSimState("idle");
    client.loadModel(activeModel.path);
  }, [activeModel, client, resetForNewRun, setSimState]);

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

  // Run-from-palette: replicate the RunButton's flow without owning
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
      console.error("failed to start run from palette:", e);
    }
  }, [client]);

  return (
    <>
      {activeWorkspace === "sim" && (
        <AppShell
          subscribe={subscribe}
          outliner={<Outliner onLoadRun={onLoadRun} />}
          viewport={<Viewport />}
          properties={<Properties />}
        />
      )}
      {activeWorkspace === "recipes" && (
        <FullWorkspaceShell subscribe={subscribe}>
          <RecipesWorkspace />
        </FullWorkspaceShell>
      )}
      <CommandPalette onRun={triggerRun} />
    </>
  );
}
