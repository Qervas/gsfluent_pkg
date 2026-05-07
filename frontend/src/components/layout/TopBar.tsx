import { useStore } from "@/lib/store";
import { RunButton } from "@/components/runs/RunButton";

export function TopBar({ subscribe }: { subscribe: (run_name: string) => void }) {
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);

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
                {activeRecipeName.startsWith("★ ") ? activeRecipeName : activeRecipeName}
              </span>
            </>
          )}
        </>
      ) : (
        <span className="text-text-secondary text-xs">no model loaded</span>
      )}
      <div className="ml-auto flex gap-2">
        <RunButton subscribe={subscribe} />
      </div>
    </div>
  );
}
