import { useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { SourceCard } from "@/components/sim/SourceCard";
import { SimulationCard } from "@/components/sim/SimulationCard";
import { Viewport } from "@/components/viewport/Viewport";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { RecipesModal } from "@/components/recipes/RecipesModal";
import { useStore } from "@/lib/store";
import { CellRef } from "@/lib/cell";
import { api } from "@/lib/api";
import type { SequenceItem, ModelItem } from "@/lib/types";

export default function App() {
  const resetForNewRun = useStore((s) => s.resetForNewRun);
  const activeModel = useStore((s) => s.activeModel);
  const activeCell = useStore((s) => s.activeCell);
  const setActiveModel = useStore((s) => s.setActiveModel);
  const setSimState = useStore((s) => s.setSimState);
  const setFpsHint = useStore((s) => s.setFpsHint);

  // Phase 3: keep fpsHint in sync with the active sequence's _meta.json
  // value. We subscribe to the same cached `["sequences"]` query the
  // SequenceTree uses, so this is essentially free — no extra fetches.
  // Falls back to 24 fps when the active cell isn't a known sequence
  // (model preview, mid-load before the list arrives).
  const { data: sequences = [] } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });
  const activeSequenceName =
    activeCell?.kind === "sequence" ? activeCell.name : null;
  useEffect(() => {
    // 12 fps is the steady-state ceiling for in-browser .gsq playback:
    // each frame decodes ~8 MB for cluster_6_15-class scenes and the
    // WASM sorter processes one frame at a time. 24 fps overruns the
    // decoder budget, stalls the main thread, and the bar looks frozen.
    // Clamp whatever meta.fps_hint declares to that ceiling.
    const MAX_PLAYBACK_FPS = 12;
    if (!activeSequenceName) {
      setFpsHint(MAX_PLAYBACK_FPS);
      return;
    }
    const seq = (sequences as SequenceItem[]).find(
      (s) => s.name === activeSequenceName,
    );
    const declared = seq?.fps_hint ?? MAX_PLAYBACK_FPS;
    setFpsHint(Math.min(declared, MAX_PLAYBACK_FPS));
  }, [activeSequenceName, sequences, setFpsHint]);

  // (Removed Cmd-R hotkey — it was clobbering the browser's native
  // reload. The recipes modal is still reachable via the Recipes
  // button in TopBar. If a hotkey ever comes back, pick something
  // that doesn't shadow a browser shortcut, e.g. plain "r" with the
  // editable-element guard.)

  // Switching to a model preview is dispatched imperatively (not via a
  // useEffect on `activeModel`), so clicking the same model twice still
  // re-fires the swap. SplatScene reacts to the activeCell change and
  // loads the new model in-browser.
  const onPickModel = useCallback(
    (m: ModelItem) => {
      setActiveModel(m);
      useStore.getState().setActiveCell(new CellRef("model", m.name));
      resetForNewRun(m.name);
      setSimState("idle");
    },
    [resetForNewRun, setActiveModel, setSimState],
  );

  // Backstop for the non-click flows (DropZone upload, path-paste): when
  // a new model object lands in the store with a different reference,
  // mirror the same swap. Skips when activeModel matches the current
  // active cell (we're already previewing it) to avoid re-loading on
  // unrelated store updates.
  useEffect(() => {
    if (!activeModel?.path) return;
    const cell = useStore.getState().activeCell;
    if (cell?.kind === "model" && cell.name === activeModel.name) return;
    useStore.getState().setActiveCell(new CellRef("model", activeModel.name));
    resetForNewRun(activeModel.name);
    setSimState("idle");
  }, [activeModel, resetForNewRun, setSimState]);

  const onLoadRun = useCallback(
    (run_name: string) => {
      resetForNewRun(run_name);
      useStore.getState().setActiveCell(new CellRef("sequence", run_name));
    },
    [resetForNewRun],
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
    st.setActiveCell(new CellRef("sequence", run_name));
    try {
      await api.runs.start({
        run_name,
        model_path: st.activeModel.path,
        recipe_data: st.activeRecipeData,
        recipe_source: st.activeRecipeName,
        particles: 200_000,
      });
    } catch (e) {
      console.error("failed to start run from palette:", e);
    }
  }, []);

  return (
    <>
      <AppShell
        sourceCard={<SourceCard onLoadRun={onLoadRun} onPickModel={onPickModel} />}
        simCard={<SimulationCard />}
        viewport={<Viewport />}
      />
      <RecipesModal />
      <CommandPalette onRun={triggerRun} />
    </>
  );
}
