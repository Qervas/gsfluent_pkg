import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Check } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

type DragKind = "ply" | "npz" | "mixed" | "unknown";

// SubtleCrypto SHA-256 of a File. Hex-encoded to match the server's
// hashlib.sha256(content).hexdigest() format so /api/models/check_hash
// can compare directly without normalization.
async function sha256OfFile(file: File): Promise<string> {
  const buf = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Compact byte formatter for the progress overlay. We always show one
// fractional digit above 1KB to keep the column width stable as the
// counter ticks up — nicer than the staircase you'd get with Math.round.
function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function safeToast(message: string, kind: "info" | "success" | "error") {
  // The toast slice was added in a previous phase; older builds (and
  // tests with a stubbed store) may not have it. Degrade to console
  // instead of crashing the whole upload flow over a missing helper.
  try {
    const t = useStore.getState().showToast;
    if (typeof t === "function") {
      t(message, kind);
      return;
    }
  } catch {
    // fall through
  }
  // eslint-disable-next-line no-console
  console.log(`[toast:${kind}] ${message}`);
}

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
  // Multi-phase upload state. The ply path goes through five stages
  // and the overlay labels each one so the user never sees a static
  // "Uploading…" while the browser is actually hashing or gzipping.
  // npz uploads stay on "uploading" since there's no client-side prep.
  type Phase = "hashing" | "checking" | "compressing" | "uploading" | "dedup-hit";
  const [phase, setPhase] = useState<Phase | null>(null);
  // Live transport progress, populated only during the "uploading"
  // phase via XHR.upload.onprogress events. Null in every other phase
  // because there are no wire bytes to count yet.
  const [progress, setProgress] = useState<
    { loaded: number; total: number } | null
  >(null);
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
        setPhase("uploading");
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
          setPhase(null);
        }
        return;
      }

      // kind === "ply"
      const ply = files.find((f) => f.name.toLowerCase().endsWith(".ply"))!;
      const cam = files.find((f) => f.name.toLowerCase() === "cameras.json");
      setBusy("ply");
      setProgress(null);
      try {
        // 1) Hash the file client-side.
        setPhase("hashing");
        const hash = await sha256OfFile(ply);

        // 2) Ask the server if it already has this exact content. Skips a
        //    potentially-multi-GB transport on identical re-drops.
        setPhase("checking");
        const check = await api.models.checkHash(hash, ply.name);
        if (check.exists && check.name) {
          setPhase("dedup-hit");
          // Cache hit: pull the full ModelItem off the listing so the
          // active-model state has every field downstream code reads.
          // Falls back to a synthesized record if list/find races.
          const all = await api.models.list();
          const existing = all.find((mm) => mm.name === check.name);
          if (existing) {
            setActiveModel(existing);
            qc.invalidateQueries({ queryKey: ["models"] });
            safeToast(`Already in library — using ${check.name}`, "info");
            return; // skip upload entirely
          }
        }

        // 3) Cache miss: gzip (api.models.upload calls back via onPhase)
        //    then XHR upload with live progress events.
        const m = await api.models.upload(ply, cam, convertYUpRef.current, {
          onPhase: (p) => setPhase(p),
          onProgress: (loaded, total) => setProgress({ loaded, total }),
        });
        setActiveModel(m);
        qc.invalidateQueries({ queryKey: ["models"] });
        safeToast(`Uploaded ${m.name}`, "success");
      } catch (err) {
        setError(
          `model upload failed: ${err instanceof Error ? err.message : String(err)}`,
        );
        setTimeout(() => setError(null), 6000);
      } finally {
        setBusy(null);
        setProgress(null);
        setPhase(null);
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
      {/* Y-up toggle for .ply uploads — only visible while a .ply drag
       *  is in progress over the viewport. Previously rendered whenever
       *  `dragKind !== "npz"`, which is true in the no-drag default
       *  (null !== "npz"), so the toggle appeared at all times and
       *  read as a global "scene up-axis" control. It is not — it only
       *  affects how the next ply upload gets re-oriented. Gate on
       *  isOver + ply so it only appears in its actual scope. */}
      {isOver && dragKind === "ply" && (
        <div
          className="absolute top-[68px] right-3 z-10"
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
          <div className="glass-card px-4 py-3 text-text-secondary text-xs min-w-[320px]">
            <UploadProgressBody busy={busy} phase={phase} progress={progress} />
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

type Phase = "hashing" | "checking" | "compressing" | "uploading" | "dedup-hit";

/** Upload progress body — one of:
 *  - hashing       : spinner + "Hashing file…"  (sha256 in browser, ~1-2s per GB)
 *  - checking      : spinner + "Checking server cache…"  (~50ms)
 *  - dedup-hit     : ✓ + "Already in library, loading…"  (transient, ~100ms)
 *  - compressing   : spinner + "Compressing…"  (gzip via CompressionStream, ~1-2s per 100MB)
 *  - uploading     : spinner + "Uploading… {%}"  + bar + bytes counter
 *
 *  Every phase has a label so the user is never left staring at a
 *  static "Uploading…" line during the multi-second prep stages. */
function UploadProgressBody({
  busy,
  phase,
  progress,
}: {
  busy: "ply" | "npz";
  phase: Phase | null;
  progress: { loaded: number; total: number } | null;
}) {
  // Map phase → label. npz uploads only ever pass through "uploading"
  // because there's no sha/gzip prep on that path.
  const phaseLabel: Record<Phase, string> =
    busy === "npz"
      ? { hashing: "", checking: "", compressing: "", uploading: "Uploading sequence…", "dedup-hit": "" }
      : {
          hashing: "Hashing file…",
          checking: "Checking server cache…",
          "dedup-hit": "Already in library, loading…",
          compressing: "Compressing…",
          uploading: "Uploading model…",
        };
  const isDedupHit = phase === "dedup-hit";
  const showBar = phase === "uploading" && progress && progress.total > 0;
  const label = (phase && phaseLabel[phase]) || (busy === "ply" ? "Preparing…" : "Uploading sequence…");
  const pct = showBar ? Math.round((progress!.loaded / progress!.total) * 100) : null;

  return (
    <div>
      <div className="flex items-center gap-2">
        {isDedupHit ? (
          <Check size={11} className="text-success shrink-0" />
        ) : (
          <Loader2 size={11} className="animate-spin text-accent shrink-0" />
        )}
        <span className="text-text-primary">{label}</span>
        {pct !== null && (
          <span className="ml-auto font-mono text-[10px] tabular-nums">{pct}%</span>
        )}
      </div>
      {showBar && progress && (
        <>
          <div className="mt-2 h-1 bg-elevated rounded overflow-hidden">
            <div
              className="h-full bg-accent transition-[width] duration-fast"
              style={{ width: `${(progress.loaded / progress.total) * 100}%` }}
            />
          </div>
          <div className="mt-1 text-[10px] text-text-muted font-mono">
            {fmtBytes(progress.loaded)} / {fmtBytes(progress.total)}
          </div>
        </>
      )}
    </div>
  );
}
