export type WorkbenchMode =
  | { kind: "idle" }
  | { kind: "model_preview"; modelName: string }
  | { kind: "sim_running"; runName: string }
  | { kind: "sim_replay"; runName: string };

type ActiveCellArg = { kind: "model" | "sequence"; name: string } | null;

/** Derive the workbench display mode from the active cell + sim state.
 *  Phase 4 dropped the legacy `simRunName` (with its `_model:` prefix
 *  hack) in favor of the typed `activeCell`; this function now consumes
 *  the cell directly. */
export function deriveMode(
  simState: string,
  activeCell: ActiveCellArg,
  nFrames: number,
): WorkbenchMode {
  if (!activeCell) return { kind: "idle" };
  if (activeCell.kind === "model") {
    return { kind: "model_preview", modelName: activeCell.name };
  }
  // activeCell.kind === "sequence"
  if (simState === "running") {
    return { kind: "sim_running", runName: activeCell.name };
  }
  if (nFrames > 1 && (simState === "done" || simState === "idle")) {
    return { kind: "sim_replay", runName: activeCell.name };
  }
  return { kind: "idle" };
}

export function modeLabel(m: WorkbenchMode): string {
  switch (m.kind) {
    case "idle":          return "idle";
    case "model_preview": return "preview";
    case "sim_running":   return "running";
    case "sim_replay":    return "replay";
  }
}

export function modeAccentClass(m: WorkbenchMode): string {
  switch (m.kind) {
    case "idle":          return "text-text-muted";
    case "model_preview": return "text-warning";   // amber: it's static, no animation expected from sim
    case "sim_running":   return "text-accent";    // cyan: live activity
    case "sim_replay":    return "text-success";   // green: completed/playing
  }
}
