import { useEffect, useRef, useState } from "react";
import { TopBar } from "./TopBar";
import { StatusPanel } from "./StatusPanel";
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
 *    z-40  <StatusPanel> floating bottom-right pill
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
  const simState = useStore((s) => s.simState);
  const simNFrames = useStore((s) => s.simNFrames);
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const simLog = useStore((s) => s.simLog);

  // Cmd/Ctrl-B + Cmd/Ctrl-I — scoped collapse toggles. Cmd/Ctrl-/ jumps
  // focus to the Outliner (skip-link, replaces the more conventional
  // anchor-based skip-link because we have no body anchor — the
  // viewport is a canvas). All three skip when the user is typing in
  // an input.
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
      } else if (e.key === "/") {
        e.preventDefault();
        if (panels.outliner === "collapsed") {
          setPanelCollapsed("outliner", false);
        }
        // Defer focus so the panel has finished transitioning open.
        requestAnimationFrame(() => {
          const el =
            document.querySelector<HTMLElement>('[aria-label="Outliner"] [role="button"], [aria-label="Outliner"] button, [aria-label="Outliner"] [tabindex="0"]') ||
            document.querySelector<HTMLElement>('[aria-label="Outliner"]');
          el?.focus();
        });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [panels.outliner, panels.properties, setPanelCollapsed]);

  // Live-region: announce sim state transitions to assistive tech.
  // We coalesce on `simState` changes (not progress ticks — those
  // would spam a screen reader). The text is short and verb-led so
  // VoiceOver/NVDA queue it without clipping.
  const lastAnnounced = useRef<string>("");
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    // Only announce on state transitions (not on every frame tick),
    // otherwise a long sim would chatter at the user once per frame.
    let msg = "";
    if (simState === "running") {
      const pct = simTotalFrames > 0
        ? Math.round((simNFrames / simTotalFrames) * 100)
        : 0;
      msg = pct > 0 ? `Simulation running, ${pct} percent.` : "Simulation started.";
    } else if (simState === "done") {
      msg = "Simulation finished.";
    } else if (simState === "error") {
      const lastLog = simLog[simLog.length - 1] || "";
      msg = lastLog ? `Simulation error: ${lastLog}` : "Simulation error.";
    } else if (simState === "cancelled") {
      msg = "Simulation cancelled.";
    } else if (simState === "idle" && lastAnnounced.current.startsWith("Simulation running")) {
      msg = "Simulation idle.";
    }
    if (msg && msg !== lastAnnounced.current) {
      lastAnnounced.current = msg;
      setAnnouncement(msg);
    }
  }, [simState, simNFrames, simTotalFrames, simLog]);

  return (
    <div className="h-screen w-screen relative bg-canvas text-text-primary text-sm overflow-hidden">
      {/* Skip-link — invisible until focused, jumps screen-reader /
          keyboard users straight into the Outliner. Companion to the
          Cmd-/ shortcut above for keyboard-only users without a
          modifier key. */}
      <a
        href="#outliner"
        onClick={(e) => {
          e.preventDefault();
          if (panels.outliner === "collapsed") {
            setPanelCollapsed("outliner", false);
          }
          requestAnimationFrame(() => {
            document
              .querySelector<HTMLElement>('[aria-label="Outliner"]')
              ?.focus();
          });
        }}
        className="absolute left-2 top-2 -translate-y-16 focus:translate-y-0 z-50
                   bg-accent text-canvas px-3 py-1.5 rounded text-xs font-medium
                   transition-transform duration-fast ease-motion shadow-glass
                   focus:outline-none focus:ring-2 focus:ring-accent-glow"
      >
        Skip to Outliner
      </a>

      {/* Live-region for sim state announcements. `polite` so it
          doesn't interrupt the user mid-action; `atomic` so each
          message reads in full instead of as a diff. */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {announcement}
      </div>

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

      {/* Status panel — z-40, compact bottom-left floating pill. Replaces
          the old fixed-bottom strip so the viewport reads as one
          continuous canvas instead of being chopped by a status row. */}
      <StatusPanel />
    </div>
  );
}
