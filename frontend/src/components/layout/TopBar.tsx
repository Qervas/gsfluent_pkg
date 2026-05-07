import { useStore } from "@/lib/store";
import { RunButton } from "@/components/runs/RunButton";
import { deriveMode, modeLabel, modeAccentClass } from "@/lib/derive-mode";

export function TopBar({ subscribe }: { subscribe: (run_name: string) => void }) {
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const simState = useStore((s) => s.simState);
  const simRunName = useStore((s) => s.simRunName);
  const frameCount = useStore((s) => s.frameXyz.size);
  const mode = deriveMode(simState, simRunName, frameCount);

  return (
    <div className="h-10 border-b border-border px-3 flex items-center gap-2 backdrop-blur bg-canvas/85 shrink-0">
      <span className="text-accent text-xs">●</span>
      <span className="font-semibold">gsfluent</span>
      <span className="text-text-muted text-xs">·</span>
      {activeModel ? (
        <>
          <span className="text-text-secondary text-xs truncate" title={activeModel.path}>
            {activeModel.name}
          </span>
          {activeRecipeName && (
            <>
              <span className="text-text-muted text-xs">·</span>
              <span className="text-text-secondary text-xs truncate">
                {activeRecipeName}
              </span>
            </>
          )}
        </>
      ) : (
        <span className="text-text-secondary text-xs">no model loaded</span>
      )}
      {mode.kind !== "idle" && (
        <span
          className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ml-2 border border-border ${modeAccentClass(mode)}`}
          title={
            mode.kind === "model_preview" ? `Static gaussian model preview: ${mode.modelName}`
            : mode.kind === "sim_running" ? `Live sim: ${mode.runName}`
            : mode.kind === "sim_replay"  ? `Replaying past run: ${mode.runName}`
            : ""
          }
        >
          {modeLabel(mode)}
        </span>
      )}
      <div className="ml-auto flex gap-2">
        <RunButton subscribe={subscribe} />
      </div>
    </div>
  );
}
