import { useEffect } from "react";
import { TopBar } from "./TopBar";
import { StatusStrip } from "./StatusStrip";
import { GlassCard } from "@/components/ui/GlassCard";
import { useStore } from "@/lib/store";

/** Stage AppShell — fullscreen viewport with floating glass cards
 *  layered above. Replaces the old react-resizable-panels split that
 *  fought the viewport-first commitment.
 *
 *  Layout:
 *    z-0   <Viewport>    inset-0, fills the screen
 *    z-20  <Outliner>    fixed left, glass card (slides off when collapsed)
 *    z-20  <Properties>  fixed right, glass card (auto-hidden when no recipe)
 *    z-20  <PlaybackDock> fixed bottom-center (Phase 4: only when sequence active)
 *    z-30  <TopBar>      fixed top, thin glass bar
 *    z-40  <StatusStrip> fixed bottom (compact, behind any modals)
 *
 *  Keyboard:
 *    Cmd/Ctrl-B  toggles Outliner collapsed/expanded
 *    Cmd/Ctrl-I  toggles Properties collapsed/expanded
 *    Cmd/Ctrl-K  opens the command palette (handled by CommandPalette)
 */
type Props = {
  outliner: React.ReactNode;
  viewport: React.ReactNode;
  properties: React.ReactNode;
  subscribe: (run_name: string) => void;
};

export function AppShell({ outliner, viewport, properties, subscribe }: Props) {
  const panels = useStore((s) => s.panels);
  const setPanelCollapsed = useStore((s) => s.setPanelCollapsed);
  const hasRecipe = useStore((s) => s.activeRecipeData != null);

  // Cmd/Ctrl-B + Cmd/Ctrl-I — scoped collapse toggles. We attach a single
  // document listener (not a per-component hook) so the shortcut works
  // regardless of focus, except when the user is typing in an input.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (!meta) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toUpperCase();
      const editable =
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
        target?.isContentEditable === true;
      if (editable) return;

      if (e.key.toLowerCase() === "b") {
        e.preventDefault();
        setPanelCollapsed("outliner", panels.outliner !== "collapsed");
      } else if (e.key.toLowerCase() === "i") {
        e.preventDefault();
        setPanelCollapsed("properties", panels.properties !== "collapsed");
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [panels.outliner, panels.properties, setPanelCollapsed]);

  return (
    <div className="h-screen w-screen relative bg-canvas text-text-primary text-sm overflow-hidden">
      {/* Fullscreen viewport — z-0 base layer */}
      <main role="main" className="absolute inset-0 z-0">
        {viewport}
      </main>

      {/* Top bar — z-30, fixed */}
      <TopBar subscribe={subscribe} />

      {/* Outliner — z-20, left-anchored glass card */}
      <GlassCard
        side="left"
        collapsed={panels.outliner === "collapsed"}
        onCollapse={() =>
          setPanelCollapsed("outliner", panels.outliner !== "collapsed")
        }
        shortcut="⌘B"
        ariaLabel="Outliner"
        className="fixed left-3 top-[68px] bottom-3 w-72 z-20"
      >
        <GlassCard.Body className="pr-6">{outliner}</GlassCard.Body>
      </GlassCard>

      {/* Properties — z-20, right-anchored glass card. Auto-hidden when
          no recipe is active (no dead controls). User can also manually
          collapse to a rail via Cmd-I. */}
      <GlassCard
        side="right"
        collapsed={!hasRecipe || panels.properties === "collapsed"}
        onCollapse={() =>
          setPanelCollapsed("properties", panels.properties !== "collapsed")
        }
        shortcut="⌘I"
        ariaLabel="Properties"
        className="fixed right-3 top-[68px] bottom-20 w-80 z-20"
      >
        <GlassCard.Body className="pl-6">{properties}</GlassCard.Body>
      </GlassCard>

      {/* Status strip — z-40, compact bottom bar with sim progress.
          Stays for now; Phase 4 may absorb its content into the
          PlaybackDock + RunButton. */}
      <StatusStrip />
    </div>
  );
}
