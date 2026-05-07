import { Upload } from "lucide-react";

export function EmptyState() {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div className="border-2 border-dashed border-border rounded p-8 text-center bg-canvas/50 backdrop-blur-sm">
        <Upload className="mx-auto mb-2 text-text-muted" size={32} />
        <div className="text-sm text-text-secondary">Drag a 3DGS .ply here</div>
        <div className="text-xs text-text-muted mt-1">or pick from the Outliner</div>
      </div>
    </div>
  );
}
