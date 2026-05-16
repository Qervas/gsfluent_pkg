import { TopBar } from "./TopBar";
import { StatusPanel } from "./StatusPanel";

/** Full-screen shell for non-Sim workspaces (Recipes). The Sim
 *  workspace uses AppShell directly with the Stage layout; this shell
 *  keeps the same TopBar chrome and the floating StatusPanel, giving
 *  `children` the whole middle band. */
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
          add 8 px gutter → 68 px. Bottom margin is just the 12 px
          gutter; the StatusPanel pill floats over content rather than
          reserving its own row. */}
      <div className="absolute inset-0 pt-[68px] pb-3 overflow-hidden">
        <div className="h-full">{children}</div>
      </div>
      <StatusPanel />
    </div>
  );
}
