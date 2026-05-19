/**
 * Zustand store — UI-only ephemera. Server-truth lives in TanStack Query.
 * NOTHING here persists to localStorage. The current v1 stack's pattern
 * of "apiBase + viserEnabled + settingsOpen in localStorage" is gone.
 */

import { create } from "zustand";

type Store = {
  commandPaletteOpen: boolean;
  sidebarCollapsed: boolean;
  selectedRunIds: string[];

  setCommandPaletteOpen: (v: boolean) => void;
  setSidebarCollapsed: (v: boolean) => void;
  toggleSelectedRun: (id: string) => void;
  clearSelectedRuns: () => void;
};

export const useStore = create<Store>((set) => ({
  commandPaletteOpen: false,
  sidebarCollapsed: false,
  selectedRunIds: [],

  setCommandPaletteOpen: (v) => set({ commandPaletteOpen: v }),
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSelectedRun: (id) =>
    set((s) => ({
      selectedRunIds: s.selectedRunIds.includes(id)
        ? s.selectedRunIds.filter((x) => x !== id)
        : [...s.selectedRunIds, id],
    })),
  clearSelectedRuns: () => set({ selectedRunIds: [] }),
}));
