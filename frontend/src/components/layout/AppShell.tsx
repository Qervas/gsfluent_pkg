import { forwardRef, useImperativeHandle, useRef } from "react";
import {
  PanelGroup,
  Panel,
  PanelResizeHandle,
  type ImperativePanelHandle,
} from "react-resizable-panels";
import { TopBar } from "./TopBar";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { StatusStrip } from "./StatusStrip";

export type AppShellHandle = {
  toggleSidebar: () => void;
  toggleInspector: () => void;
};

type Props = {
  outliner: React.ReactNode;
  viewport: React.ReactNode;
  properties: React.ReactNode;
  subscribe: (run_name: string) => void;
};

export const AppShell = forwardRef<AppShellHandle, Props>(function AppShell(
  { outliner, viewport, properties, subscribe },
  ref,
) {
  const sidebarRef = useRef<ImperativePanelHandle>(null);
  const inspectorRef = useRef<ImperativePanelHandle>(null);

  useImperativeHandle(
    ref,
    () => ({
      toggleSidebar: () => {
        const p = sidebarRef.current;
        if (!p) return;
        if (p.isCollapsed()) p.expand();
        else p.collapse();
      },
      toggleInspector: () => {
        const p = inspectorRef.current;
        if (!p) return;
        if (p.isCollapsed()) p.expand();
        else p.collapse();
      },
    }),
    [],
  );

  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary text-sm">
      <TopBar subscribe={subscribe} />
      <WorkspaceTabs />
      <PanelGroup direction="horizontal" autoSaveId="gsfluent.split.h" className="flex-1 min-h-0">
        <Panel
          ref={sidebarRef}
          defaultSize={18}
          minSize={12}
          collapsible
          collapsedSize={0}
          className="border-r border-border"
        >
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
        <Panel
          ref={inspectorRef}
          defaultSize={24}
          minSize={16}
          collapsible
          collapsedSize={0}
          className="border-l border-border"
        >
          <div className="h-full overflow-y-auto">{properties}</div>
        </Panel>
      </PanelGroup>
      <StatusStrip />
    </div>
  );
});
