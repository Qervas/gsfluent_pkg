import { create } from "zustand";
import type { ModelItem, StaticAttrs, Workspace } from "./types";

type SimState = "idle" | "running" | "done" | "error" | "cancelled";
export type RenderMode = "points" | "splat";

// Phase 3 playback speed: 1× = sequence's fps_hint (24 by default).
// Multiplier scales the inter-frame DELAY, not the index step — so 4× still
// hits every frame, just faster. Stays a finite enum so the dropdown is
// 5 fixed cells and `,` / `.` keys can cycle deterministically.
export type SpeedX = 0.25 | 0.5 | 1 | 2 | 4;
export const SPEED_X_VALUES: SpeedX[] = [0.25, 0.5, 1, 2, 4];

type State = {
  // Workspace selection
  activeWorkspace: Workspace;
  setActiveWorkspace: (w: Workspace) => void;

  // Selected items
  activeModel: ModelItem | null;
  activeRecipeName: string | null;
  activeRecipeData: Record<string, unknown> | null;

  // Sim status
  simState: SimState;
  simRunName: string | null;
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

  // Active render path. "points" uses the lightweight Three.js Points
  // pipeline that streams over the websocket and supports per-frame
  // position updates (sim playback). "splat" loads the raw .ply via
  // GET /api/models/file and renders proper anisotropic gaussian splats
  // — only available for static model preview, not sim runs.
  renderMode: RenderMode;

  // Setters
  setActiveModel: (m: ModelItem | null) => void;
  setActiveRecipe: (n: string | null, d: Record<string, unknown> | null) => void;
  setSimState: (s: SimState) => void;
  appendLog: (line: string) => void;
  putFrame: (idx: number, xyz: Float32Array) => void;
  setStaticAttrs: (a: StaticAttrs) => void;
  setCurrentFrame: (i: number) => void;
  setPlaying: (p: boolean) => void;
  setSceneScale: (diag: number, center: [number, number, number]) => void;
  setSceneFloor: (z: number) => void;
  setRenderMode: (m: RenderMode) => void;
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
};

export const useStore = create<State>((set) => ({
  activeWorkspace: "sim",
  setActiveWorkspace: (w) => set({ activeWorkspace: w }),
  activeModel: null,
  activeRecipeName: null,
  activeRecipeData: null,
  simState: "idle",
  simRunName: null,
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
  renderMode: "points",

  setActiveModel: (m) => set({ activeModel: m }),
  setActiveRecipe: (n, d) => set({ activeRecipeName: n, activeRecipeData: d }),
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
  setStaticAttrs: (a) => set({ staticAttrs: a }),
  setCurrentFrame: (i) => set({ currentFrameIdx: i }),
  setPlaying: (p) => set({ playing: p }),
  setSceneScale: (diag, center) => set({ sceneScale: diag, sceneCenter: center }),
  setSceneFloor: (z) => set({ sceneFloor: z }),
  setRenderMode: (mode) => set({ renderMode: mode }),
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
