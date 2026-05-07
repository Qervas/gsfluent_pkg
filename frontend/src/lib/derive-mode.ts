export type WorkbenchMode =
  | { kind: "idle" }
  | { kind: "model_preview"; modelName: string }
  | { kind: "sim_running"; runName: string }
  | { kind: "sim_replay"; runName: string };

export function deriveMode(
  simState: string,
  simRunName: string | null,
  frameCount: number,
): WorkbenchMode {
  if (simRunName && simRunName.startsWith("_model:")) {
    return { kind: "model_preview", modelName: simRunName.slice("_model:".length) };
  }
  if (simState === "running" && simRunName) {
    return { kind: "sim_running", runName: simRunName };
  }
  if (simRunName && frameCount > 1 && (simState === "done" || simState === "idle")) {
    return { kind: "sim_replay", runName: simRunName };
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
