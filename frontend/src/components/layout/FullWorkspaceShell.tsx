import { TopBar } from "./TopBar";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { StatusStrip } from "./StatusStrip";

export function FullWorkspaceShell({
  subscribe,
  children,
}: {
  subscribe: (run_name: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary text-sm">
      <TopBar subscribe={subscribe} />
      <WorkspaceTabs />
      <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
      <StatusStrip />
    </div>
  );
}
