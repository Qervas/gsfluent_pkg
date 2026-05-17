import { useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { SourceCard } from "@/components/sim/SourceCard";
import { SimulationCard } from "@/components/sim/SimulationCard";
import { Viewport } from "@/components/viewport/Viewport";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { RecipesModal } from "@/components/recipes/RecipesModal";
import { useStreamClient } from "@/lib/use-stream";
import { useStore } from "@/lib/store";
import { api } from "@/lib/api";
import type { SequenceItem, ModelItem } from "@/lib/types";

export default function App() {
  const client = useStreamClient();
  const resetForNewRun = useStore((s) => s.resetForNewRun);
  const activeModel = useStore((s) => s.activeModel);
  const setActiveModel = useStore((s) => s.setActiveModel);
  const setSimState = useStore((s) => s.setSimState);
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

  // Cmd-R toggles the recipes modal. Registered globally so it works
  // anywhere in the app, with the standard editable-element guard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (!meta || e.key.toLowerCase() !== "r") return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toUpperCase();
      const editable =
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
        target?.isContentEditable === true;
      if (editable) return;
      e.preventDefault();
      const st = useStore.getState();
      st.setRecipesModalOpen(!st.recipesModalOpen);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Switching to a model preview is dispatched imperatively (not via a
  // useEffect on `activeModel`), so clicking the same model twice still
  // re-fires the swap. Path: user picks a sequence, then clicks the
  // already-active model in ModelTree — the React reference didn't
  // change, so an effect on `activeModel` would no-op. Calling this
  // function directly from the click handler dodges that.
  const onPickModel = useCallback(
    (m: ModelItem) => {
      setActiveModel(m);
      resetForNewRun(`_model:${m.name}`);
      setSimState("idle");
      useStore.getState().setSimKind("preview");
      client.loadModel(m.path);
    },
    [client, resetForNewRun, setActiveModel, setSimState],
  );

  // Backstop for the non-click flows (DropZone upload, path-paste): when
  // a new model object lands in the store with a different reference,
  // mirror the same swap. Skips when activeModel matches the current
  // sim run (we're already previewing it) to avoid re-loading on
  // unrelated store updates.
  useEffect(() => {
    if (!activeModel?.path) return;
    if (simRunName === `_model:${activeModel.name}`) return;
    resetForNewRun(`_model:${activeModel.name}`);
    setSimState("idle");
    useStore.getState().setSimKind("preview");
    client.loadModel(activeModel.path);
  }, [activeModel, simRunName, client, resetForNewRun, setSimState]);

  const subscribe = useCallback(
    (run_name: string) => client.subscribe(run_name),
    [client],
  );

  const onLoadRun = useCallback(
    (run_name: string) => {
      resetForNewRun(run_name);
      useStore.getState().setSimKind("replay");
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
    useStore.getState().setSimKind("sim");
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
      <AppShell
        subscribe={subscribe}
        sourceCard={<SourceCard onLoadRun={onLoadRun} onPickModel={onPickModel} />}
        simCard={<SimulationCard subscribe={subscribe} />}
        viewport={<Viewport />}
      />
      <RecipesModal />
      <CommandPalette onRun={triggerRun} />
    </>
  );
}
