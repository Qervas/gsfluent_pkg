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
      <PanelGroup direction="horizontal" autoSaveId="gsfluent.split.h" className="flex-1 min-h-0">
        <Panel defaultSize={18} minSize={12} className="border-r border-border">
          {/* Inner wrapper: h-full + overflow-y-auto reliably triggers scroll
              when content exceeds the Panel height. Tailwind's overflow-auto
              applied directly to <Panel> doesn't work because the panel's
              own positioning intercepts it. */}
          <div className="h-full overflow-y-auto">{outliner}</div>
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent/40 transition-colors" />
        <Panel defaultSize={58} minSize={30}>
          <div className="h-full">{viewport}</div>
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-accent/40 transition-colors" />
        <Panel defaultSize={24} minSize={16} className="border-l border-border">
          <div className="h-full overflow-y-auto">{properties}</div>
        </Panel>
      </PanelGroup>
      <StatusStrip />
    </div>
  );
}
