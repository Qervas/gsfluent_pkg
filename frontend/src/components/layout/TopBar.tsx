import { useStore } from "@/lib/store";
import { RunButton } from "@/components/runs/RunButton";
import { StatusPill } from "@/components/layout/StatusPill";
import { ChevronRight } from "lucide-react";
import { useRecipeDirty } from "@/lib/use-recipe-dirty";

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
export function TopBar() {
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeCell = useStore((s) => s.activeCell);
  const recipeDirty = useRecipeDirty();

  // Breadcrumb sequence segment is the active cell's name only when it
  // is a sequence — static model previews already surface via the
  // model segment, no point showing twice.
  const simSegment =
    activeCell?.kind === "sequence" ? activeCell.name : null;

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

      <button
        type="button"
        onClick={() => useStore.getState().setRecipesModalOpen(true)}
        className="px-2 py-1 rounded-md text-xs font-medium text-text-secondary hover:text-text-primary hover:bg-elevated/60 flex items-center gap-1"
        title="Open recipe library (⌘R)"
      >
        📚 Recipes
      </button>

      {/* Breadcrumb — model / recipe / sequence. Clickable segments are
          wired in Phase 4; for now they're plain text. */}
      <Breadcrumb
        items={[
          activeModel?.name && { label: activeModel.name, kind: "model" as const },
          activeRecipeName && {
            label: activeRecipeName.replace(/^★ /, "") + (recipeDirty ? " *" : ""),
            kind: "recipe" as const,
            dirty: recipeDirty,
          },
          simSegment && { label: simSegment, kind: "sequence" as const },
        ].filter(Boolean) as Array<{ label: string; kind: "model" | "recipe" | "sequence"; dirty?: boolean }>}
      />

      <div className="flex-1" />

      <StatusPill />
      <RunButton />
    </header>
  );
}

function Breadcrumb({
  items,
}: {
  items: { label: string; kind: "model" | "recipe" | "sequence"; dirty?: boolean }[];
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
                : it.dirty
                ? "text-warning"
                : "text-text-secondary")
            }
            title={
              it.dirty
                ? `${it.kind}: ${it.label} (unsaved edits)`
                : `${it.kind}: ${it.label}`
            }
          >
            {it.label}
          </span>
        </span>
      ))}
    </nav>
  );
}
