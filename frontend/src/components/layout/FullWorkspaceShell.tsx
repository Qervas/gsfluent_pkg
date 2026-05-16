import { TopBar } from "./TopBar";
import { StatusStrip } from "./StatusStrip";

/** Full-screen shell for non-Sim workspaces (Recipes). The Sim
 *  workspace uses AppShell directly with the Stage layout; this shell
 *  keeps the same TopBar + StatusStrip chrome but gives `children` the
 *  whole middle band. TopBar/StatusStrip are fixed-position so we just
 *  pad the content area to avoid them. */
export function FullWorkspaceShell({
  subscribe,
  children,
}: {
  subscribe: (run_name: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="h-screen w-screen relative bg-canvas text-text-primary text-sm overflow-hidden">
      <TopBar subscribe={subscribe} />
      {/* Top: clear the 12 (top-3) + 48 (h-12 topbar) = 60 px chrome,
          add 8 px gutter → 68 px. Bottom: clear the 32 px StatusStrip. */}
      <div className="absolute inset-0 pt-[68px] pb-8 overflow-hidden">
        <div className="h-full">{children}</div>
      </div>
      <StatusStrip />
    </div>
  );
}
