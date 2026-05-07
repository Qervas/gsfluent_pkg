import { PanelGroup, Panel, PanelResizeHandle } from "react-resizable-panels";
import { TopBar } from "./TopBar";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { StatusStrip } from "./StatusStrip";

export function AppShell({
  outliner,
  viewport,
  properties,
  subscribe,
}: {
  outliner: React.ReactNode;
  viewport: React.ReactNode;
  properties: React.ReactNode;
  subscribe: (run_name: string) => void;
}) {
  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary text-sm">
      <TopBar subscribe={subscribe} />
      <WorkspaceTabs />
      <PanelGroup direction="horizontal" autoSaveId="gsfluent.split.h" className="flex-1">
        <Panel defaultSize={18} minSize={12} className="border-r border-border overflow-auto">
          {outliner}
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent/40 transition-colors" />
        <Panel defaultSize={58} minSize={30}>
          {viewport}
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent/40 transition-colors" />
        <Panel defaultSize={24} minSize={16} className="border-l border-border overflow-auto">
          {properties}
        </Panel>
      </PanelGroup>
      <StatusStrip />
    </div>
  );
}
