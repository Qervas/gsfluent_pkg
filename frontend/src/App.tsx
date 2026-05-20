import { useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { SourceCard } from "@/components/sim/SourceCard";
import { SimulationCard } from "@/components/sim/SimulationCard";
import { Viewport } from "@/components/viewport/Viewport";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { RecipesModal } from "@/components/recipes/RecipesModal";
import { SettingsModal } from "@/components/layout/SettingsModal";
import { useStore } from "@/lib/store";
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
    // 12 fps is the steady-state ceiling for viser→browser splat
    // streaming over WAN: each frame push is ~8 MB for cluster_6_15-
    // class scenes, the link is ~100 Mbps, and the WASM sorter chews
    // through one frame at a time. 24 fps overruns the pipe, stalls
    // the main thread for seconds at a time, and the bar looks frozen.
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
  // re-fires the swap. Viser handles the actual model load via the
  // /set push from ViserSplatScene's effect on activeCell change.
  const onPickModel = useCallback(
    (m: ModelItem) => {
      setActiveModel(m);
      useStore.getState().setActiveCell({ kind: "model", name: m.name });
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
    useStore.getState().setActiveCell({ kind: "model", name: activeModel.name });
    resetForNewRun(activeModel.name);
    setSimState("idle");
  }, [activeModel, resetForNewRun, setSimState]);

  const onLoadRun = useCallback(
    (run_name: string) => {
      resetForNewRun(run_name);
      useStore.getState().setActiveCell({ kind: "sequence", name: run_name });
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
    st.setActiveCell({ kind: "sequence", name: run_name });
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
      <SettingsModal />
      <CommandPalette onRun={triggerRun} />
    </>
  );
}
