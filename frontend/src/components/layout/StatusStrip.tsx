export function StatusStrip() {
  return (
    <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted shrink-0">
      <span className="text-accent">●</span>
      <span>idle</span>
      <span className="ml-auto">⌘K</span>
    </div>
  );
}
