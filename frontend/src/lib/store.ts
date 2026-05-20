import { create } from "zustand";
import type { ModelItem, Workspace } from "./types";

type SimState = "idle" | "running" | "done" | "error" | "cancelled";

// Phase 3 playback speed: 1× = sequence's fps_hint (24 by default).
// Multiplier scales the inter-frame DELAY, not the index step — so 4× still
// hits every frame, just faster. Stays a finite enum so the dropdown is
// 5 fixed cells and `,` / `.` keys can cycle deterministically.
export type SpeedX = 0.25 | 0.5 | 1 | 2 | 4;
export const SPEED_X_VALUES: SpeedX[] = [0.25, 0.5, 1, 2, 4];

type State = {
  /** Polled from viser's /state endpoint by ViserSplatScene's mount
   *  effect. Source of truth for cell + frame + n_frames now that the
   *  websocket positions stream is gone. */
  viserState: { cell: string | null; frame: number; n_frames: number };
  setViserState: (s: { cell: string | null; frame: number; n_frames: number }) => void;

  /** Manual on/off for the viser splat viewer iframe. When `false` the
   *  iframe is unmounted (no WebGL, no /state polling, no sorter WASM).
   *  Lets the user keep working in the rest of the app when the viser
   *  renderer crashes or NaN's. Persisted to localStorage. */
  viserEnabled: boolean;
  setViserEnabled: (b: boolean) => void;


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
  activeCell: { kind: "model" | "sequence"; name: string } | null;
  setActiveCell: (cell: { kind: "model" | "sequence"; name: string } | null) => void;
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

  // Playback cursor — viser owns the actual frame buffer; this is just
  // the canonical scrubber index. ViserSplatScene forwards each change
  // to viser's /set endpoint.
  currentFrameIdx: number;
  playing: boolean;

  // Phase 3 playback slice. `playing` and `currentFrameIdx` above are kept
  // (deliberately not renamed) so existing consumers don't break; the new
  // fields below extend the slice with speed/loop/fpsHint plus a
  // `scrubbing` flag the PlaybackDriver checks before advancing — when the
  // user is mid-drag, we suspend autoplay so their drag wins.
  speedX: SpeedX;
  loop: boolean;
  fpsHint: number;
  scrubbing: boolean;

  // Scene scale — diag of the active model's bbox. Phase 1 used this to
  // size the three.js grid + camera fade; viser owns its own camera fit
  // now, so these fields are vestigial but cheap to keep around in case
  // a future overlay needs world-scale info.
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
  setCurrentFrame: (i: number) => void;
  setPlaying: (p: boolean) => void;
  setSceneScale: (diag: number, center: [number, number, number]) => void;
  setSceneFloor: (z: number) => void;
  // Phase 3 setters.
  setSpeedX: (s: SpeedX) => void;
  setLoop: (loop: boolean) => void;
  setFpsHint: (fps: number) => void;
  setScrubbing: (b: boolean) => void;
  // Step the current frame by `delta` (positive or negative). Clamped to
  // [0, viserState.n_frames - 1] so we don't run off either end —
  // viser is the truth-source for frame availability.
  stepFrame: (delta: number) => void;
  resetForNewRun: (name: string) => void;
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

function loadViserEnabled(): boolean {
  try {
    const raw = localStorage.getItem("gsfluent.viserEnabled");
    if (raw === "false") return false;
    if (raw === "true") return true;
  } catch { /* private mode / no storage */ }
  return true; // default: on
}

export const useStore = create<State>((set) => ({
  viserState: { cell: null, frame: 0, n_frames: 0 },
  setViserState: (s) => set({ viserState: s }),
  viserEnabled: loadViserEnabled(),
  setViserEnabled: (b) =>
    set(() => {
      try { localStorage.setItem("gsfluent.viserEnabled", b ? "true" : "false"); } catch { /* private mode */ }
      return { viserEnabled: b };
    }),
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
  setActiveCell: (cell) => set({ activeCell: cell }),
  simNFrames: 0,
  simTotalFrames: 150,
  simStage: "idle",
  simEtaSec: null,
  simLog: [],
  simStartedAt: null,
  simFirstFrameAt: null,
  simLastLogAt: null,
  currentFrameIdx: 0,
  playing: true,
  speedX: 1,
  loop: true,
  // Default playback at 12 fps, not 24. Each frame pushes the full
  // splat-centers array over the WS (~8 MB for cluster_6_15-class
  // scenes); 24 fps × 8 MB = ~200 MB/s, which neither the WAN link
  // nor the in-browser WASM sorter can sustain, so playback stalls
  // for seconds at a time. 12 fps is roughly the steady-state ceiling
  // here and stays smooth.
  fpsHint: 12,
  scrubbing: false,
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
  setCurrentFrame: (i) => set({ currentFrameIdx: i }),
  setPlaying: (p) => set({ playing: p }),
  setSceneScale: (diag, center) => set({ sceneScale: diag, sceneCenter: center }),
  setSceneFloor: (z) => set({ sceneFloor: z }),
  setSpeedX: (s) => set({ speedX: s }),
  setLoop: (loop) => set({ loop }),
  setFpsHint: (fps) => set({ fpsHint: fps > 0 ? fps : 24 }),
  setScrubbing: (b) => set({ scrubbing: b }),
  stepFrame: (delta) =>
    set((st) => {
      const upper = Math.max(0, st.viserState.n_frames - 1);
      const next = Math.min(upper, Math.max(0, st.currentFrameIdx + delta));
      return { currentFrameIdx: next };
    }),
  resetForNewRun: (_name) =>
    set({
      simState: "running",
      simNFrames: 0,
      simLog: [],
      currentFrameIdx: 0,
      simStage: "starting",
      simStartedAt: Date.now(),
      simFirstFrameAt: null,
      simLastLogAt: null,
      sceneFloor: 0,
      // Don't reset speedX / loop / fpsHint here — those are user-tweaked
      // playback prefs that should persist across runs. fpsHint is owned
      // by the SequenceTree onPick handler / live-sim pump and overwritten
      // when a new sequence is loaded.
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
