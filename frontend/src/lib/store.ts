import { create } from "zustand";
import type { ModelItem, Workspace, PlaybackState } from "./types";
import { CellRef } from "./cell";

type SimState = "idle" | "running" | "done" | "error" | "cancelled";

type State = {
  /** Sequence metadata published by SplatScene (n_frames). The frame cursor
   *  lives in SplatScene's rAF loop, not here — playback no longer round-trips
   *  through React state. */
  playbackState: PlaybackState;
  setPlaybackState: (s: PlaybackState) => void;

  // Workspace selection
  activeWorkspace: Workspace;
  setActiveWorkspace: (w: Workspace) => void;

  // Selected items
  activeModel: ModelItem | null;
  activeRecipeName: string | null;
  activeRecipeData: Record<string, unknown> | null;
  // Pristine snapshot of the loaded recipe (what the server has).
  // Compared against `activeRecipeData` to compute the dirty flag the
  // Properties panel surfaces. `null` when no recipe is loaded.
  activeRecipePristine: Record<string, unknown> | null;

  // Sim override engine. `simRecipeBaseline` is a snapshot of the recipe
  // as selected (server-authoritative). `simOverrides` is a sparse map of
  // user edits; only keys the user touched live here. effective config is
  // computed by useOverrides() as {...baseline, ...overrides}. Cleared on
  // recipe switch, Reset all, Save as new, page reload.
  simRecipeBaseline: Record<string, unknown> | null;
  simOverrides:      Record<string, unknown>;
  setSimRecipeBaseline: (data: Record<string, unknown> | null) => void;
  setOverride:    (key: string, value: unknown) => void;
  clearOverride:  (key: string) => void;
  clearAllOverrides: () => void;

  // Sim status
  simState: SimState;
  /** Canonical "what's loaded in the viewer". Encodes both the kind of
   *  resource (model preview vs simulation sequence) and its name.
   *  Null when nothing is loaded. Phase 4 promoted this to the sole
   *  source of truth, dropping the legacy `simRunName` / `simKind` pair. */
  activeCell: CellRef | null;
  setActiveCell: (cell: CellRef | null) => void;
  simNFrames: number;
  simTotalFrames: number;
  simStage: string;
  simEtaSec: number | null;
  simLog: string[];
  simStartedAt: number | null;
  simFirstFrameAt: number | null;
  // Wall-clock timestamp of the last log line received from the wrapper.
  // Used by the heartbeat indicator to surface "stalled" cases — Taichi/
  // Warp JIT compilation, fuse-drain hangs, etc. — that don't crash but
  // also produce no output for minutes at a stretch.
  simLastLogAt: number | null;

  // Coarse playback intent consumed by SplatScene's rAF loop (read via
  // getState each tick — no per-frame React state). `playing`/`loop` toggle
  // the loop; bumping `resetNonce` jumps the playhead back to frame 0.
  playing: boolean;
  loop: boolean;
  fpsHint: number;
  resetNonce: number;

  // Scene scale — diag of the active model's bbox. Phase 1 used this to
  // size the three.js grid + camera fade. These fields are vestigial
  // but cheap to keep around in case a future overlay needs world-scale
  // info.
  sceneScale: number;
  sceneCenter: [number, number, number];
  // World-Z of the bbox bottom — the "floor" that the model sits on.
  // Vestigial alongside sceneScale; kept for the same reason.
  sceneFloor: number;

  // Stage redesign: floating-panel collapse state. Persisted to
  // localStorage so the user's last layout choice survives reload.
  // Outliner / Properties each have two states (collapsed / expanded);
  // Playback dock is auto-shown when a sequence is active and auto-
  // hidden after camera idle (handled in the dock component, not stored).
  panels: {
    outliner: "expanded" | "collapsed";
    properties: "expanded" | "collapsed";
  };
  setPanelCollapsed: (panel: "outliner" | "properties", collapsed: boolean) => void;

  recipesModalOpen: boolean;
  setRecipesModalOpen: (open: boolean) => void;

  runBlockedByJson: boolean;
  setRunBlockedByJson: (v: boolean) => void;

  // Transient bottom-center toast for ephemeral feedback (e.g., "Loaded
  // X into Sim" when the recipes modal closes). Auto-dismissed by the
  // renderer after ~3s.
  toast: { message: string; kind: "info" | "success" | "error" } | null;
  showToast: (message: string, kind?: "info" | "success" | "error") => void;
  clearToast: () => void;

  // Setters
  setActiveModel: (m: ModelItem | null) => void;
  // Edit-style setter: panels call this to update individual fields.
  // Touches activeRecipeData only — pristine snapshot is preserved so
  // the dirty flag (data !== pristine) lights up.
  setActiveRecipe: (n: string | null, d: Record<string, unknown> | null) => void;
  // Load-style setter: called when a fresh recipe is fetched from the
  // server. Updates BOTH activeRecipeData and activeRecipePristine, so
  // the dirty flag goes back to false even if the new data happens to
  // equal the old.
  loadActiveRecipe: (n: string | null, d: Record<string, unknown> | null) => void;
  // Called after a successful Save — re-snapshots pristine = current
  // data without changing activeRecipeData.
  markRecipeClean: () => void;
  setSimState: (s: SimState) => void;
  appendLog: (line: string) => void;
  setPlaying: (p: boolean) => void;
  setSceneScale: (diag: number, center: [number, number, number]) => void;
  setSceneFloor: (z: number) => void;
  setLoop: (loop: boolean) => void;
  setFpsHint: (fps: number) => void;
  // Jump the playhead back to frame 0 (SplatScene's loop watches resetNonce).
  requestReset: () => void;
  // `totalFrames`: the recipe's `frame_num` for the run being launched, so the
  // progress UI shows a real denominator immediately ("0 / 240") instead of
  // starting at the legacy hardcoded 150. Omit for recovery/replay paths that
  // don't know the count yet — `simTotalFrames` stays 0 and the tqdm parser
  // populates it from the first sim log line.
  resetForNewRun: (name: string, totalFrames?: number) => void;
  // Frame-progress setter driven by the run-log parser (tqdm `n/total`
  // lines). Bumps simFirstFrameAt on the 0→positive transition so the
  // ETA computation has a wall-clock anchor.
  setSimProgress: (nFrames: number, totalFrames: number) => void;
};

/** Read the persisted panel-collapse state from localStorage, defaulting
 *  cleanly if missing / malformed. Both panels open by default — first-
 *  time users see everything; collapse is a power-user move. */
function loadPanels(): State["panels"] {
  try {
    const raw = localStorage.getItem("gsfluent.panels");
    if (raw) {
      const parsed = JSON.parse(raw);
      const valid = (v: unknown) => v === "expanded" || v === "collapsed";
      if (valid(parsed?.outliner) && valid(parsed?.properties)) {
        return parsed as State["panels"];
      }
    }
  } catch { /* private mode / no storage */ }
  return { outliner: "expanded", properties: "expanded" };
}

export const useStore = create<State>((set) => ({
  playbackState: { n_frames: 0 },
  setPlaybackState: (s) => set({ playbackState: s }),
  activeWorkspace: "sim",
  setActiveWorkspace: (w) => set({ activeWorkspace: w }),
  activeModel: null,
  activeRecipeName: null,
  activeRecipeData: null,
  activeRecipePristine: null,
  simRecipeBaseline: null,
  simOverrides:      {},
  setSimRecipeBaseline: (data) =>
    set({ simRecipeBaseline: data, simOverrides: {} }),
  setOverride: (key, value) =>
    set((s) => ({ simOverrides: { ...s.simOverrides, [key]: value } })),
  clearOverride: (key) =>
    set((s) => {
      const next = { ...s.simOverrides };
      delete next[key];
      return { simOverrides: next };
    }),
  clearAllOverrides: () => set({ simOverrides: {} }),
  simState: "idle",
  activeCell: null,
  // Skip the set when the new ref names the same cell — keeps zustand's
  // identity-compare selectors from firing on no-op rewrites (e.g. when
  // SourceCard re-renders and re-dispatches the active sequence).
  setActiveCell: (cell) =>
    set((st) => {
      const cur = st.activeCell;
      if (cell === null && cur === null) return {};
      if (cell && cur && cell.equals(cur)) return {};
      return { activeCell: cell };
    }),
  simNFrames: 0,
  // 0 = unknown. Either the tqdm parser overwrites it once the sim logs its
  // first "n/total" line, OR the run trigger passes the recipe's frame_num
  // through resetForNewRun(name, frame_num). The old 150 default leaked into
  // the progress UI as a misleading denominator on any non-150 run.
  simTotalFrames: 0,
  simStage: "idle",
  simEtaSec: null,
  simLog: [],
  simStartedAt: null,
  simFirstFrameAt: null,
  simLastLogAt: null,
  playing: true,
  loop: true,
  // App-level default/cap for fps. The actual playback rate is each .gsq's
  // own header fps_hint, read in-loop by SplatScene (no network per frame
  // anymore, so no artificial throttle needed).
  fpsHint: 12,
  resetNonce: 0,
  sceneScale: 10,
  sceneCenter: [0, 0, 0],
  sceneFloor: 0,
  panels: loadPanels(),

  recipesModalOpen: false,
  setRecipesModalOpen: (open) => set({ recipesModalOpen: open }),

  runBlockedByJson: false,
  setRunBlockedByJson: (v) => set({ runBlockedByJson: v }),

  toast: null,
  showToast: (message, kind = "info") => set({ toast: { message, kind } }),
  clearToast: () => set({ toast: null }),

  setPanelCollapsed: (panel, collapsed) =>
    set((st) => {
      const next = {
        ...st.panels,
        [panel]: collapsed ? "collapsed" : "expanded",
      };
      try { localStorage.setItem("gsfluent.panels", JSON.stringify(next)); } catch { /* private mode */ }
      return { panels: next };
    }),

  setActiveModel: (m) => set({ activeModel: m }),
  setActiveRecipe: (n, d) => set({ activeRecipeName: n, activeRecipeData: d }),
  loadActiveRecipe: (name, data) =>
    set({
      activeRecipeName:     name,
      activeRecipeData:     data,
      activeRecipePristine: data ? JSON.parse(JSON.stringify(data)) : null,
      // Snapshot the recipe as the sim baseline + clear any overrides
      // from the previous recipe (they don't apply to a new baseline).
      simRecipeBaseline:    data ? JSON.parse(JSON.stringify(data)) : null,
      simOverrides:         {},
    }),
  markRecipeClean: () =>
    set((st) => ({
      activeRecipePristine: st.activeRecipeData
        ? JSON.parse(JSON.stringify(st.activeRecipeData))
        : null,
    })),
  setSimState: (s) => set({ simState: s }),
  appendLog: (line) =>
    set((st) => ({
      simLog: [...st.simLog.slice(-1999), line],
      simLastLogAt: Date.now(),
    })),
  setPlaying: (p) => set({ playing: p }),
  setSceneScale: (diag, center) => set({ sceneScale: diag, sceneCenter: center }),
  setSceneFloor: (z) => set({ sceneFloor: z }),
  setLoop: (loop) => set({ loop }),
  setFpsHint: (fps) => set({ fpsHint: fps > 0 ? fps : 24 }),
  requestReset: () => set((st) => ({ resetNonce: st.resetNonce + 1 })),
  resetForNewRun: (_name, totalFrames) =>
    set({
      simState: "running",
      simNFrames: 0,
      // Pre-populate the denominator from the recipe's frame_num so the
      // status pill shows "0 / 240" the instant the run starts, before the
      // first tqdm line lands. Recovery/replay paths that don't know the
      // count pass nothing → 0 (the parser fills it in).
      simTotalFrames: totalFrames && totalFrames > 0 ? totalFrames : 0,
      simLog: [],
      simStage: "starting",
      simStartedAt: Date.now(),
      simFirstFrameAt: null,
      simLastLogAt: null,
      sceneFloor: 0,
      // Don't reset loop / fpsHint here — those are user-tweaked playback
      // prefs that persist across runs. fpsHint is owned by App's sequence
      // watcher / live-sim pump and overwritten when a new sequence loads.
    }),
  setSimProgress: (nFrames, totalFrames) =>
    set((st) => ({
      simNFrames: nFrames,
      simTotalFrames: totalFrames > 0 ? totalFrames : st.simTotalFrames,
      // Anchor the ETA clock when the first frame lands. After that we
      // leave it alone so the running average stays meaningful even if
      // a few mid-run tqdm lines arrive out of order.
      simFirstFrameAt:
        st.simFirstFrameAt ?? (nFrames > 0 ? Date.now() : null),
    })),
}));
