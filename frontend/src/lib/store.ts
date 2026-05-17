import { create } from "zustand";
import type { ModelItem, StaticAttrs, Workspace } from "./types";

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
  /** Distinguishes how the current activity began. Set by call sites
   *  that initiate (Run button, sequence click, model pick). Used by
   *  StatusPanel to decide whether to show sim-progress UI ("sim") or
   *  a quieter replay indicator ("replay") or nothing ("preview"/null).
   *  The stream protocol doesn't carry this — server replays look
   *  identical to live sim runs on the wire. */
  simKind: "sim" | "replay" | "preview" | null;
  setSimKind: (kind: "sim" | "replay" | "preview" | null) => void;
  simRunName: string | null;
  /** Replaces the simRunName-as-string overload. Encodes both what
   *  kind of resource is loaded (model vs sequence) and its name,
   *  with no prefix-shenanigans. Null when nothing is loaded. */
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

  // Frames
  staticAttrs: StaticAttrs | null;
  frameXyz: Map<number, Float32Array>;
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

  // Scene scale — diag of the active model's bbox. Used by the Grid + camera
  // far/fade so they follow the model whether it lives at world (3, 3, 3) or
  // (3460, 29045, 5). Set by SplatScene's auto-fit effect.
  sceneScale: number;
  sceneCenter: [number, number, number];
  // World-Z of the bbox bottom — the "floor" that the model sits on.
  // Grid is positioned here so it sits underneath the model rather than
  // bisecting it at z=0 (which would be inside the model for anything with
  // negative-z extent, hiding the grid behind opaque points).
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

  // Last-known Points-mode camera state. Written continuously by
  // SplatScene as the user orbits, read by Viewport.tsx's mode-toggle
  // effect so the Splats iframe gets POSTed the same viewpoint. Null
  // until the user has interacted with the Points-mode controls at
  // least once (initial auto-fit doesn't count — we want the user's
  // chosen view to carry over, not the auto-framed one).
  pointsCamera: {
    position: [number, number, number];
    target:   [number, number, number];
  } | null;

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
  putFrame: (idx: number, xyz: Float32Array) => void;
  setStaticAttrs: (a: StaticAttrs) => void;
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
  // [0, frameXyz.size - 1] so we don't run off either end. `frameXyz.size`
  // is the truth-source for the upper bound — for a live sim it grows as
  // frames land, so this clamp tracks production naturally.
  stepFrame: (delta: number) => void;
  resetForNewRun: (name: string) => void;
  setPointsCamera: (cam: State["pointsCamera"]) => void;
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
  viserState: { cell: null, frame: 0, n_frames: 0 },
  setViserState: (s) => set({ viserState: s }),
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
  simKind: null,
  setSimKind: (kind) => set({ simKind: kind }),
  simRunName: null,
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
  staticAttrs: null,
  frameXyz: new Map(),
  currentFrameIdx: 0,
  playing: true,
  speedX: 1,
  loop: true,
  fpsHint: 24,
  scrubbing: false,
  sceneScale: 10,
  sceneCenter: [0, 0, 0],
  sceneFloor: 0,
  pointsCamera: null,
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
  putFrame: (idx, xyz) =>
    set((st) => {
      const m = new Map(st.frameXyz);
      m.set(idx, xyz);
      return {
        frameXyz: m,
        simNFrames: m.size,
        simFirstFrameAt: st.simFirstFrameAt ?? Date.now(),
      };
    }),
  // Clearing pointsCamera here means a new model load triggers a fresh
  // auto-fit in SplatScene instead of reusing a viewpoint from the
  // previously-loaded scene (which would likely be off-scale).
  setStaticAttrs: (a) => set({ staticAttrs: a, pointsCamera: null }),
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
      const upper = Math.max(0, st.frameXyz.size - 1);
      const next = Math.min(upper, Math.max(0, st.currentFrameIdx + delta));
      return { currentFrameIdx: next };
    }),
  setPointsCamera: (cam) => set({ pointsCamera: cam }),
  resetForNewRun: (name) =>
    set({
      simRunName: name,
      simState: "running",
      simNFrames: 0,
      simLog: [],
      staticAttrs: null,
      frameXyz: new Map(),
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
}));
