import { type ReactNode, type CSSProperties } from "react";
import { ChevronLeft, ChevronRight, ChevronDown } from "lucide-react";

/** Floating glass-card primitive — the visual unit every Stage panel
 *  (Outliner, Properties, Playback) sits inside. Same surface, same
 *  shadow, same slide-in motion, configured by `side` so the same
 *  component handles left/right/bottom-anchored panels.
 *
 *  Why one primitive instead of three: keeps the visual language
 *  cohesive (one place to tune blur, border, shadow, easing) and means
 *  we only need to test one set of animation + a11y semantics.
 *
 *  Collapse: optional. When `onCollapse` is provided, a chevron in the
 *  header toggles collapsed state. `collapsed=true` slides the card off
 *  in `side`'s direction; the parent should switch to a slim rail or
 *  pill in the same space.
 */
export type GlassCardSide = "left" | "right" | "bottom";

export function GlassCard({
  side = "left",
  collapsed = false,
  onCollapse,
  shortcut,
  className = "",
  style,
  ariaLabel,
  children,
}: {
  side?: GlassCardSide;
  collapsed?: boolean;
  onCollapse?: () => void;
  shortcut?: string;
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
  children: ReactNode;
}) {
  // Slide direction matches the panel's anchor. Collapsed cards slide
  // off-screen rather than fade out — keeps the affordance of "this
  // panel is over there, off-stage" without taking pointer events.
  const collapsedTransform =
    side === "left"   ? "-translate-x-[110%]" :
    side === "right"  ? "translate-x-[110%]"  :
                        "translate-y-[110%]";
  const transform = collapsed ? collapsedTransform : "translate-x-0 translate-y-0";

  return (
    <aside
      role="complementary"
      aria-label={ariaLabel}
      aria-expanded={!collapsed}
      className={
        "glass-card flex flex-col " +
        "transition-[transform,opacity] duration-panel ease-motion " +
        (collapsed ? "opacity-0 pointer-events-none " : "opacity-100 ") +
        transform + " " +
        className
      }
      style={style}
    >
      {children}
      {onCollapse && (
        <CollapseButton side={side} collapsed={collapsed} shortcut={shortcut} onClick={onCollapse} />
      )}
    </aside>
  );
}

/** Subcomponent: card header. Compose with `GlassCard.Header` for the
 *  drag-grip + title + actions row at the top of each panel. */
GlassCard.Header = function GlassCardHeader({
  title,
  actions,
  className = "",
}: {
  title?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header
      className={
        "flex items-center gap-2 px-3 h-9 border-b border-border/40 " +
        "text-xs uppercase tracking-wider text-text-muted " +
        className
      }
    >
      {/* Drag-grip dots — purely visual cue v1; drag-to-reposition lands
          in a later phase if we keep wanting it. */}
      <span className="inline-flex flex-col gap-0.5 shrink-0 opacity-40">
        <span className="block w-1 h-1 rounded-full bg-current" />
        <span className="block w-1 h-1 rounded-full bg-current" />
      </span>
      <span className="flex-1 truncate">{title}</span>
      {actions}
    </header>
  );
};

/** Subcomponent: scrollable card body. Keeps overflow + padding
 *  consistent across panels. */
GlassCard.Body = function GlassCardBody({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={"flex-1 min-h-0 overflow-y-auto p-3 " + className}>
      {children}
    </div>
  );
};

function CollapseButton({
  side,
  collapsed,
  shortcut,
  onClick,
}: {
  side: GlassCardSide;
  collapsed: boolean;
  shortcut?: string;
  onClick: () => void;
}) {
  // Pick the icon + position so the chevron always points "off-stage".
  // (Left-anchored panel collapses leftward, so chevron points left.)
  const Icon =
    side === "left"   ? (collapsed ? ChevronRight : ChevronLeft) :
    side === "right"  ? (collapsed ? ChevronLeft  : ChevronRight) :
                        ChevronDown;

  const tooltip = shortcut
    ? `${collapsed ? "Expand" : "Collapse"} (${shortcut})`
    : (collapsed ? "Expand" : "Collapse");

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={tooltip}
      title={tooltip}
      className={
        "absolute top-2 z-10 p-1 rounded text-text-muted " +
        "hover:bg-elevated hover:text-text-primary " +
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 " +
        "transition-colors duration-fast " +
        // Park the button on the OUTSIDE edge of the card so it stays
        // visible when collapsed (otherwise it slides off with the card).
        (side === "left"  ? "right-2 " :
         side === "right" ? "left-2 "  :
                            "right-2 ")
      }
    >
      <Icon size={14} />
    </button>
  );
}
