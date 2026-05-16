import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

type DragKind = "ply" | "npz" | "mixed" | "unknown";

/**
 * Window-level drag-drop overlay for both model and sequence uploads.
 *
 * - `.ply` (optionally with a sibling `cameras.json`) → POST /api/models/upload
 * - `.npz` (a pre-built playback cache) → POST /api/sequences/upload-npz
 *
 * The overlay sniffs the dragged file's extension during dragenter so
 * the hint can tell the user what's about to happen *before* they let
 * go. Mixed drags (e.g. .ply + .npz at the same time) are rejected with
 * a clear error rather than silently dropping one of them.
 *
 * Listens on `window` for dragover/dragleave/drop so the user can drop
 * anywhere in the app, not just on the viewport.
 */
export function DropZone() {
  const [isOver, setIsOver] = useState(false);
  const [dragKind, setDragKind] = useState<DragKind>("unknown");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "ply" | "npz">(null);
  // Persistent Y-up toggle that applies to .ply uploads. Drag-drop
  // fires straight into the upload mutation with no chance for the
  // user to click an option mid-drop, so the toggle has to be set
  // BEFORE dragging. It's hidden during .npz drops because the
  // sequence path stores raw arrays — orientation is baked into the
  // file at simulation time.
  const [convertYUp, setConvertYUp] = useState(false);
  const convertYUpRef = useRef(false);
  convertYUpRef.current = convertYUp;
  const setActiveModel = useStore((s) => s.setActiveModel);
  const qc = useQueryClient();

  useEffect(() => {
    let dragCounter = 0;

    const classify = (names: string[]): DragKind => {
      const hasPly = names.some((n) => n.endsWith(".ply"));
      const hasNpz = names.some((n) => n.endsWith(".npz"));
      if (hasPly && hasNpz) return "mixed";
      if (hasPly) return "ply";
      if (hasNpz) return "npz";
      return "unknown";
    };

    const onEnter = (e: DragEvent) => {
      e.preventDefault();
      if (!e.dataTransfer?.types.includes("Files")) return;
      dragCounter++;
      // The DataTransferItemList exposes `kind` + `type` during
      // dragenter, but file *names* only become available on drop in
      // most browsers. Fall back to MIME type sniffing here so the
      // overlay can at least say "file" when names are absent.
      const items = Array.from(e.dataTransfer.items ?? []);
      const types = items.map((it) => (it.type || "").toLowerCase());
      // Some browsers report empty type for .npz / .ply. Use a best
      // effort here; final routing happens at drop time when File.name
      // is available.
      const hint = types.some((t) => t.includes("zip") || t.includes("octet"))
        ? "npz"
        : types.some((t) => t.includes("ply"))
        ? "ply"
        : "unknown";
      setDragKind(hint as DragKind);
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
        setDragKind("unknown");
      }
    };
    const onDrop = async (e: DragEvent) => {
      e.preventDefault();
      dragCounter = 0;
      setIsOver(false);
      setDragKind("unknown");
      setError(null);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length === 0) return;

      const lowered = files.map((f) => f.name.toLowerCase());
      const kind = classify(lowered);

      if (kind === "mixed") {
        setError(
          "Mixed drop: drop .ply files OR a .npz, not both at the same time.",
        );
        setTimeout(() => setError(null), 5000);
        return;
      }

      if (kind === "unknown") {
        setError(
          `Unsupported drop: ${files.map((f) => f.name).join(", ")} ` +
            `(expected .ply or .npz)`,
        );
        setTimeout(() => setError(null), 5000);
        return;
      }

      if (kind === "npz") {
        const npz = files.find((f) => f.name.toLowerCase().endsWith(".npz"))!;
        setBusy("npz");
        try {
          await api.sequences.uploadNpz(npz);
          qc.invalidateQueries({ queryKey: ["sequences"] });
        } catch (err) {
          setError(
            `sequence upload failed: ${err instanceof Error ? err.message : String(err)}`,
          );
          setTimeout(() => setError(null), 6000);
        } finally {
          setBusy(null);
        }
        return;
      }

      // kind === "ply"
      const ply = files.find((f) => f.name.toLowerCase().endsWith(".ply"))!;
      const cam = files.find((f) => f.name.toLowerCase() === "cameras.json");
      setBusy("ply");
      try {
        const m = await api.models.upload(ply, cam, convertYUpRef.current);
        setActiveModel(m);
        qc.invalidateQueries({ queryKey: ["models"] });
      } catch (err) {
        setError(
          `model upload failed: ${err instanceof Error ? err.message : String(err)}`,
        );
        setTimeout(() => setError(null), 6000);
      } finally {
        setBusy(null);
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
      {/* Persistent Y-up toggle, top-right of the viewport. Only
       *  relevant for .ply (model) uploads; hidden while a .npz drag
       *  is in progress to avoid suggesting it does anything.
       *  Parked below the TopBar (top-[104px] = 68 TopBar bottom +
       *  ~36 RenderModeToggle height) so it sits in a column with
       *  the render-mode toggle. */}
      {dragKind !== "npz" && (
        <div
          className="absolute top-[104px] right-3 z-10"
        >
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
      )}
      {error && (
        <div className="absolute inset-0 flex items-end justify-center pointer-events-none p-4">
          <div className="bg-error/15 border border-error/40 text-error text-xs px-3 py-2 rounded max-w-md text-center">
            {error}
          </div>
        </div>
      )}
      {busy && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="glass-card px-4 py-2 text-text-secondary text-xs">
            Uploading {busy === "ply" ? "model" : "sequence"}…
          </div>
        </div>
      )}
      {isOver && !busy && <DropPreview kind={dragKind} convertYUp={convertYUp} />}
    </>
  );
}

function DropPreview({
  kind,
  convertYUp,
}: {
  kind: DragKind;
  convertYUp: boolean;
}) {
  // Color + copy switch by kind so the user sees, at drop time, which
  // pipeline they're about to enter. Browsers don't always reveal the
  // file name on dragenter, so "unknown" still renders a neutral hint
  // and lets the post-drop classify() catch malformed drags.
  const palette =
    kind === "npz"
      ? {
          border: "border-violet-400",
          bg: "bg-violet-400/10",
          fg: "text-violet-300",
        }
      : kind === "mixed"
      ? {
          border: "border-error",
          bg: "bg-error/10",
          fg: "text-error",
        }
      : {
          border: "border-accent",
          bg: "bg-accent/10",
          fg: "text-accent",
        };

  const title =
    kind === "npz"
      ? "Drop .npz to register as sequence"
      : kind === "ply"
      ? "Drop .ply (optionally with cameras.json) to upload model"
      : kind === "mixed"
      ? "Drop one type at a time: .ply OR .npz"
      : "Drop .ply (model) or .npz (sequence)";

  const sub =
    kind === "ply" && convertYUp
      ? "Y-up source: will convert to Z-up at import"
      : kind === "npz"
      ? "Will be mmap'd by the viser playback worker"
      : null;

  return (
    <div
      className={
        "absolute inset-0 border-2 border-dashed rounded pointer-events-none flex flex-col items-center justify-center backdrop-blur-sm gap-1.5 " +
        palette.border +
        " " +
        palette.bg
      }
    >
      <div className={"font-medium text-sm " + palette.fg}>{title}</div>
      {sub && <div className={"text-[11px] opacity-80 " + palette.fg}>{sub}</div>}
    </div>
  );
}
