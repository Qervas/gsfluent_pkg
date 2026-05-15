import { useEffect, useRef, useState } from "react";
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
  // Persistent toggle that applies to every drop until unchecked.
  // Drag-drop fires straight into the upload mutation with no chance
  // for the user to click an option mid-drop, so the user has to set
  // this BEFORE dragging. The toggle is always visible at the top-
  // right of the viewport so it's discoverable without obscuring the
  // canvas.
  const [convertYUp, setConvertYUp] = useState(false);
  const convertYUpRef = useRef(false);
  convertYUpRef.current = convertYUp;
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
        // Read from the ref so the value at drop-time wins, not the
        // value at effect-mount time.
        const m = await api.models.upload(ply, cam, convertYUpRef.current);
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

  return (
    <>
      {/* Persistent Y-up toggle, top-right of the viewport. Always
       *  visible (not just during drag) so the user can tick it
       *  BEFORE starting the drag — there's no opportunity to click
       *  anything once the file is mid-air. */}
      <div className="absolute top-2 right-2 z-10">
        <label
          className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer select-none bg-elevated/70 border border-border rounded px-1.5 py-0.5 backdrop-blur-sm"
          title="Source is Y-up (PhysGaussian/Inria); convert to Z-up at import"
        >
          <input
            type="checkbox"
            checked={convertYUp}
            onChange={(e) => setConvertYUp(e.target.checked)}
            className="accent-accent"
          />
          Y-up
        </label>
      </div>
      {error && (
        <div className="absolute inset-0 flex items-end justify-center pointer-events-none p-4">
          <div className="bg-error/15 border border-error/40 text-error text-xs px-3 py-2 rounded">
            {error}
          </div>
        </div>
      )}
      {isOver && (
        <div className="absolute inset-0 bg-accent/10 border-2 border-accent border-dashed rounded pointer-events-none flex flex-col items-center justify-center backdrop-blur-sm gap-1">
          <div className="text-accent font-medium text-sm">
            Drop .ply (optionally with cameras.json) to upload
          </div>
          {convertYUp && (
            <div className="text-accent/80 text-[11px]">
              Y-up source: will convert to Z-up at import
            </div>
          )}
        </div>
      )}
    </>
  );
}
