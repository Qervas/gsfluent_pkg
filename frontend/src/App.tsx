// src/App.tsx — minimal Blender-layout skeleton, full implementation in Task 2.3
export default function App() {
  return (
    <div className="h-screen w-screen flex flex-col bg-canvas text-text-primary text-sm">
      <div className="h-10 border-b border-border px-3 flex items-center gap-2 backdrop-blur bg-canvas/85">
        <span className="text-accent">●</span>
        <span className="font-semibold">gsfluent</span>
        <span className="text-text-muted">·</span>
        <span className="text-text-secondary text-xs">no model loaded</span>
      </div>
      <div className="h-8 border-b border-border px-3 flex items-center gap-4 text-xs">
        <span className="text-accent border-b-2 border-accent pb-0.5">Sim</span>
        <span className="text-text-muted">Compare (soon)</span>
        <span className="text-text-muted">Render (soon)</span>
        <span className="text-text-muted">Recipes (soon)</span>
      </div>
      <div className="flex-1 grid grid-cols-[200px_1fr_280px]">
        <div className="border-r border-border p-3 text-xs text-text-secondary">Outliner</div>
        <div className="bg-elevated"></div>
        <div className="border-l border-border p-3 text-xs text-text-secondary">Properties</div>
      </div>
      <div className="h-8 border-t border-border px-3 flex items-center gap-3 text-xs text-text-muted">
        <span className="text-accent">●</span>
        <span>idle</span>
        <span className="ml-auto">⌘K</span>
      </div>
    </div>
  );
}
