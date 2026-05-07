import { create } from "zustand";
import type { ModelItem, StaticAttrs } from "./types";

type SimState = "idle" | "running" | "done" | "error" | "cancelled";

type State = {
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

  // Frames
  staticAttrs: StaticAttrs | null;
  frameXyz: Map<number, Float32Array>;
  currentFrameIdx: number;
  playing: boolean;

  // Setters
  setActiveModel: (m: ModelItem | null) => void;
  setActiveRecipe: (n: string | null, d: Record<string, unknown> | null) => void;
  setSimState: (s: SimState) => void;
  appendLog: (line: string) => void;
  putFrame: (idx: number, xyz: Float32Array) => void;
  setStaticAttrs: (a: StaticAttrs) => void;
  setCurrentFrame: (i: number) => void;
  setPlaying: (p: boolean) => void;
  resetForNewRun: (name: string) => void;
};

export const useStore = create<State>((set) => ({
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
  staticAttrs: null,
  frameXyz: new Map(),
  currentFrameIdx: 0,
  playing: true,

  setActiveModel: (m) => set({ activeModel: m }),
  setActiveRecipe: (n, d) => set({ activeRecipeName: n, activeRecipeData: d }),
  setSimState: (s) => set({ simState: s }),
  appendLog: (line) => set((st) => ({ simLog: [...st.simLog.slice(-1999), line] })),
  putFrame: (idx, xyz) =>
    set((st) => {
      const m = new Map(st.frameXyz);
      m.set(idx, xyz);
      return { frameXyz: m, simNFrames: m.size };
    }),
  setStaticAttrs: (a) => set({ staticAttrs: a }),
  setCurrentFrame: (i) => set({ currentFrameIdx: i }),
  setPlaying: (p) => set({ playing: p }),
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
    }),
}));
