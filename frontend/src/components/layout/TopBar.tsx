import { useStore } from "@/lib/store";
import { RunButton } from "@/components/runs/RunButton";
import { StatusPill } from "@/components/layout/StatusPill";
import { ChevronRight } from "lucide-react";
import type { Workspace } from "@/lib/types";

/** Unified top bar (Stage redesign Phase 2). One thin (h-12) row pinned
 *  to the top of the viewport that holds everything the user needs to
 *  see at all times:
 *
 *    [Brand·dot] · [ws-chips] · [breadcrumb] ← flex-1 → [StatusPill] [Run]
 *
 *  Replaces the previous TopBar + WorkspaceTabs split. The breadcrumb
 *  surfaces the current model/recipe/sequence selection — clicking a
 *  segment jumps focus to the matching panel (Phase 4 wires the focus
 *  jump; Phase 2 just shows the text). */
export function TopBar({ subscribe }: { subscribe: (run_name: string) => void }) {
  const activeWorkspace = useStore((s) => s.activeWorkspace);
  const setActiveWorkspace = useStore((s) => s.setActiveWorkspace);
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const simRunName = useStore((s) => s.simRunName);

  // Breadcrumb segments. Skip the `_model:foo` synthetic name we use
  // for static model previews — those are the same as the activeModel
  // segment, no point showing twice.
  const simSegment =
    simRunName && !simRunName.startsWith("_model:") ? simRunName : null;

  return (
    <header
      role="banner"
      className="fixed top-3 left-3 right-3 z-30 h-12 glass-topbar rounded-xl
                 flex items-center gap-3 px-4 text-sm"
    >
      {/* Brand */}
      <div className="flex items-center gap-2 shrink-0 select-none">
        <span
          className="w-2.5 h-2.5 rounded-full shadow-accent-glow-soft"
          style={{
            background: "linear-gradient(135deg, #22d3ee 0%, #a855f7 100%)",
          }}
        />
        <span className="font-semibold tracking-tight">gsfluent</span>
      </div>

      {/* Workspace chip group — replaces the old WorkspaceTabs strip */}
      <WorkspaceChips active={activeWorkspace} onChange={setActiveWorkspace} />

      {/* Breadcrumb — model / recipe / sequence. Clickable segments are
          wired in Phase 4; for now they're plain text. */}
      <Breadcrumb
        items={[
          activeModel?.name && { label: activeModel.name, kind: "model" as const },
          activeRecipeName && { label: activeRecipeName.replace(/^★ /, ""), kind: "recipe" as const },
          simSegment && { label: simSegment, kind: "sequence" as const },
        ].filter(Boolean) as { label: string; kind: "model" | "recipe" | "sequence" }[]}
      />

      <div className="flex-1" />

      <StatusPill />
      <RunButton subscribe={subscribe} />
    </header>
  );
}

function WorkspaceChips({
  active,
  onChange,
}: {
  active: Workspace;
  onChange: (w: Workspace) => void;
}) {
  const chips: { id: Workspace; label: string }[] = [
    { id: "sim", label: "Sim" },
    { id: "recipes", label: "Recipes" },
  ];
  return (
    <div
      role="tablist"
      aria-label="Workspace"
      className="flex items-center gap-1 p-1 bg-elevated/40 rounded-lg shrink-0"
    >
      {chips.map((c) => {
        const isActive = active === c.id;
        return (
          <button
            key={c.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => onChange(c.id)}
            className={
              "px-3 py-1 rounded-md text-xs font-medium transition-colors duration-fast " +
              (isActive
                ? "bg-accent/15 text-accent"
                : "text-text-secondary hover:text-text-primary hover:bg-elevated/60")
            }
          >
            {c.label}
          </button>
        );
      })}
    </div>
  );
}

function Breadcrumb({
  items,
}: {
  items: { label: string; kind: "model" | "recipe" | "sequence" }[];
}) {
  if (items.length === 0) {
    return (
      <span className="text-text-muted text-xs italic shrink-0">
        no model loaded
      </span>
    );
  }
  return (
    <nav
      aria-label="Current selection"
      className="flex items-center gap-1.5 min-w-0 overflow-hidden text-xs"
    >
      {items.map((it, i) => (
        <span key={i} className="flex items-center gap-1.5 min-w-0">
          {i > 0 && (
            <ChevronRight
              size={12}
              className="text-text-muted/60 shrink-0"
              aria-hidden
            />
          )}
          <span
            className={
              "truncate font-mono " +
              (it.kind === "sequence"
                ? "text-accent"
                : "text-text-secondary")
            }
            title={`${it.kind}: ${it.label}`}
          >
            {it.label}
          </span>
        </span>
      ))}
    </nav>
  );
}
