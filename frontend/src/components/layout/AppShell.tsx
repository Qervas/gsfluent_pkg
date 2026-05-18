import { useEffect, useRef, useState } from "react";
import { TopBar } from "./TopBar";
import { StatusPanel } from "./StatusPanel";
import { GlassCard } from "@/components/ui/GlassCard";
import { useStore } from "@/lib/store";
import { useRunLogPoller } from "@/lib/use-run-log";
import { useSyncProgressPoller } from "@/lib/use-sync-progress";

/** Stage AppShell — fullscreen viewport with a single left-anchored
 *  glass card layered above. Phase 3 of the sim-workspace-redesign
 *  collapsed the old left Outliner + right Properties cards into one
 *  rail containing SourceCard above and SimulationCard below.
 *
 *  Layout:
 *    z-0   <Viewport>     inset-0, fills the screen
 *    z-20  <Sim panel>    fixed left, glass card stacking SourceCard
 *                         (top) + SimulationCard (bottom). Slides off
 *                         when collapsed.
 *    z-20  <PlaybackDock> fixed bottom-center (Phase 4: only when sequence active)
 *    z-30  <TopBar>       fixed top, thin glass bar
 *    z-40  <StatusPanel>  floating bottom-right pill
 *
 *  Keyboard:
 *    Cmd/Ctrl-B  toggles the Sim panel collapsed/expanded
 *    Cmd/Ctrl-K  opens the command palette (handled by CommandPalette)
 */
type Props = {
  sourceCard: React.ReactNode;
  simCard:    React.ReactNode;
  viewport:   React.ReactNode;
};

export function AppShell({ sourceCard, simCard, viewport }: Props) {
  const panels = useStore((s) => s.panels);
  const setPanelCollapsed = useStore((s) => s.setPanelCollapsed);
  const simState = useStore((s) => s.simState);
  const simNFrames = useStore((s) => s.simNFrames);
  const simTotalFrames = useStore((s) => s.simTotalFrames);
  const simLog = useStore((s) => s.simLog);

  // Stream the server's run.log into the workbench console while a sim
  // is active. Drives the StatusPanel's console drawer; without this
  // hook the drawer sits permanently at "(no output yet)".
  useRunLogPoller();
  // Surface laptop-side sync_daemon download progress into the same
  // console — interleaved [sync] lines next to the [sim] ones. Without
  // this the user can't tell whether a 2.9 GB .npz download is making
  // progress or stalled.
  useSyncProgressPoller();

  // Cmd/Ctrl-B — collapse toggle for the unified Sim panel.
  // Cmd/Ctrl-/ jumps focus into the Sim panel (skip-link, replaces the
  // more conventional anchor-based skip-link because we have no body
  // anchor — the viewport is a canvas). Both skip when the user is
  // typing in an input.
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
      } else if (e.key === "/") {
        e.preventDefault();
        if (panels.outliner === "collapsed") {
          setPanelCollapsed("outliner", false);
        }
        // Defer focus so the panel has finished transitioning open.
        requestAnimationFrame(() => {
          const el =
            document.querySelector<HTMLElement>('[aria-label="Sim panel"] [role="button"], [aria-label="Sim panel"] button, [aria-label="Sim panel"] [tabindex="0"]') ||
            document.querySelector<HTMLElement>('[aria-label="Sim panel"]');
          el?.focus();
        });
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [panels.outliner, setPanelCollapsed]);

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
          keyboard users straight into the Sim panel. Companion to the
          Cmd-/ shortcut above for keyboard-only users without a
          modifier key. */}
      <a
        href="#sim-panel"
        onClick={(e) => {
          e.preventDefault();
          if (panels.outliner === "collapsed") {
            setPanelCollapsed("outliner", false);
          }
          requestAnimationFrame(() => {
            document
              .querySelector<HTMLElement>('[aria-label="Sim panel"]')
              ?.focus();
          });
        }}
        className="absolute left-2 top-2 -translate-y-16 focus:translate-y-0 z-50
                   bg-accent text-canvas px-3 py-1.5 rounded text-xs font-medium
                   transition-transform duration-fast ease-motion shadow-glass
                   focus:outline-none focus:ring-2 focus:ring-accent-glow"
      >
        Skip to Sim panel
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
      <TopBar />

      {/* Left rail — single glass card containing SourceCard above and
          SimulationCard below. Properties has moved into SimulationCard;
          the right side of the viewport now has no glass card at all. */}
      <GlassCard
        side="left"
        collapsed={panels.outliner === "collapsed"}
        onCollapse={() => setPanelCollapsed("outliner", panels.outliner !== "collapsed")}
        shortcut="⌘B"
        ariaLabel="Sim panel"
        className="fixed left-3 top-[68px] bottom-3 w-80 z-20 flex flex-col"
      >
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex-1 min-h-0 overflow-y-auto border-b border-border">
            {sourceCard}
          </div>
          <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
            {simCard}
          </div>
        </div>
      </GlassCard>

      {/* Status panel — z-40, compact bottom-left floating pill. Replaces
          the old fixed-bottom strip so the viewport reads as one
          continuous canvas instead of being chopped by a status row. */}
      <StatusPanel />

      {/* Transient toast — bottom-center, auto-dismisses after 3s */}
      <ToastRenderer />
    </div>
  );
}

function ToastRenderer() {
  const toast = useStore((s) => s.toast);
  const clear = useStore((s) => s.clearToast);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => clear(), 3000);
    return () => clearTimeout(t);
  }, [toast, clear]);
  if (!toast) return null;
  const color =
    toast.kind === "success" ? "border-success/40 bg-success/10 text-success"
    : toast.kind === "error" ? "border-error/40 bg-error/10 text-error"
    : "border-border bg-elevated text-text-primary";
  return (
    <div
      role="status"
      className={`fixed bottom-16 left-1/2 -translate-x-1/2 z-[70] px-4 py-2 rounded border text-xs font-medium backdrop-blur-sm ${color}`}
    >
      {toast.message}
    </div>
  );
}
