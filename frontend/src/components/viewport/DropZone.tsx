import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

/**
 * Window-level drag-drop overlay for .ply uploads.
 *
 * Accepts a single .ply file, optionally accompanied by a cameras.json
 * dropped together in the same drag. If cameras.json is omitted a
 * synthetic placeholder is generated server-side from the ply bbox.
 *
 * Listens on `window` for dragover/dragleave/drop so the user can drop
 * anywhere in the app, not just on the viewport. While a drag is in
 * progress, the component renders a translucent cyan overlay across
 * the viewport with a hint. On drop the files are uploaded via
 * api.models.upload, the resulting model is set active in the store,
 * and the models query is invalidated so the Outliner refreshes.
 */
export function DropZone() {
  const [isOver, setIsOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setActiveModel = useStore((s) => s.setActiveModel);
  const qc = useQueryClient();

  useEffect(() => {
    let dragCounter = 0;

    const onEnter = (e: DragEvent) => {
      e.preventDefault();
      // Only react to file drags, not text/element drags from inside the app.
      if (!e.dataTransfer?.types.includes("Files")) return;
      dragCounter++;
      setIsOver(true);
    };
    const onOver = (e: DragEvent) => {
      e.preventDefault();
    };
    const onLeave = (e: DragEvent) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        setIsOver(false);
      }
    };
    const onDrop = async (e: DragEvent) => {
      e.preventDefault();
      dragCounter = 0;
      setIsOver(false);
      setError(null);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length === 0) return;
      const ply = files.find((f) => f.name.toLowerCase().endsWith(".ply"));
      const cam = files.find((f) => f.name.toLowerCase() === "cameras.json");
      if (!ply) {
        setError(`expected a .ply file, got ${files.map((f) => f.name).join(", ")}`);
        setTimeout(() => setError(null), 4000);
        return;
      }
      try {
        const m = await api.models.upload(ply, cam);
        setActiveModel(m);
        qc.invalidateQueries({ queryKey: ["models"] });
      } catch (e) {
        setError(`upload failed: ${e instanceof Error ? e.message : String(e)}`);
        setTimeout(() => setError(null), 6000);
      }
    };

    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [setActiveModel, qc]);

  if (!isOver && !error) return null;

  if (error) {
    return (
      <div className="absolute inset-0 flex items-end justify-center pointer-events-none p-4">
        <div className="bg-error/15 border border-error/40 text-error text-xs px-3 py-2 rounded">
          {error}
        </div>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 bg-accent/10 border-2 border-accent border-dashed rounded pointer-events-none flex items-center justify-center backdrop-blur-sm">
      <div className="text-accent font-medium text-sm">
        Drop .ply (optionally with cameras.json) to upload
      </div>
    </div>
  );
}
