import { useStore } from "./store";

/** Returns the active cell + its wire-format name (with `model:` /
 *  `sequence:` prefix) for forwarding to viser's /set endpoint.
 *
 *  Cells on the wire MUST carry the kind prefix — viser uses it to
 *  decide whether to fetch a .ply or mmap a .gsq. The store holds a
 *  `CellRef` which encapsulates the round-trip; everything that talks
 *  to viser must use this hook (or `getActiveCellWireName`) rather
 *  than concatenating `${kind}:${name}` by hand. */
export function useActiveCell() {
  const activeCell = useStore((s) => s.activeCell);
  const setActiveCell = useStore((s) => s.setActiveCell);
  return {
    activeCell,
    setActiveCell,
    /** Wire-format cell name (e.g. "model:tower_01"). Null when no cell. */
    wireName: activeCell?.wire ?? null,
    /** True when the current activity is a finished sequence (replay). */
    isSequence: activeCell?.kind === "sequence",
    /** True when the current activity is a static-model preview. */
    isModel: activeCell?.kind === "model",
  };
}

/** Imperative form — useful when not in a React component. */
export function getActiveCellWireName(): string | null {
  return useStore.getState().activeCell?.wire ?? null;
}
