import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useActiveCell } from "@/lib/use-active-cell";

type Transform =
  | "y_up_to_z_up"
  | "rotate_x_pos_90"
  | "rotate_x_neg_90"
  | "rotate_y_pos_90"
  | "rotate_y_neg_90"
  | "rotate_z_pos_90"
  | "rotate_z_neg_90"
  | "rotate_x_180"
  | "rotate_y_180"
  | "rotate_z_180";

const ROTATION_ROWS: Array<{
  axis: "X" | "Y" | "Z";
  neg90: Transform;
  pos90: Transform;
  half: Transform;
}> = [
  {
    axis: "X",
    neg90: "rotate_x_neg_90",
    pos90: "rotate_x_pos_90",
    half: "rotate_x_180",
  },
  {
    axis: "Y",
    neg90: "rotate_y_neg_90",
    pos90: "rotate_y_pos_90",
    half: "rotate_y_180",
  },
  {
    axis: "Z",
    neg90: "rotate_z_neg_90",
    pos90: "rotate_z_pos_90",
    half: "rotate_z_180",
  },
];

/** Viewport overlay: fix a static model's orientation in place.
 *
 *  The model is overwritten on the server (positions + gaussian quaternions
 *  rotated) and the splat fetch is cache-busted by the new sha256, so the
 *  cloud reloads upright. Repeatable by design — orientation is a visual/user
 *  decision, so apply, look, and adjust in 90° steps. Only shown for model
 *  cells, not sequence playback. */
export function ReorientControls() {
  // All hooks first — before any conditional return (Rules of Hooks).
  const { activeCell, isModel } = useActiveCell();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<Transform | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!isModel || !activeCell) return null;

  const apply = async (transform: Transform) => {
    setBusy(transform);
    setErr(null);
    try {
      await api.models.reorient(activeCell.name, transform);
      // New sha256 lands in the models list → SplatScene's model effect re-runs
      // (modelSha dep) and refetches the rotated cloud.
      await qc.invalidateQueries({ queryKey: ["models"] });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const panel =
    "rounded-md bg-elevated/80 backdrop-blur border border-white/10 " +
    "px-2.5 py-2 shadow-lg";
  const btn =
    "h-7 min-w-9 px-2 text-xs rounded-md bg-background/50 border border-white/10 " +
    "text-text-secondary hover:text-text-primary hover:border-white/20 transition-colors " +
    "disabled:opacity-50 disabled:cursor-not-allowed";
  const axisLabel = "w-4 text-[10px] font-medium text-text-muted text-center";

  return (
    // Anchored top-RIGHT, below the header: the fixed TopBar (z-30) covers the
    // top strip and the left sidebar (collapsible) overlays the left of the
    // full-width viewport, so top-left is hidden. The right side below the
    // header is the reliably-clear zone. z-20 sits above the splat canvas.
    <div className="absolute top-16 right-4 z-20 flex flex-col gap-1.5 items-end">
      <div className={panel}>
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <span className="text-[10px] uppercase text-text-muted">Rotate</span>
          <button
            type="button"
            className={btn}
            disabled={busy !== null}
            onClick={() => apply("y_up_to_z_up")}
            title="Shortcut: rotate Y-up source into Z-up."
          >
            Y→Z
          </button>
        </div>
        <div className="grid gap-1">
          {ROTATION_ROWS.map((row) => (
            <div key={row.axis} className="flex items-center gap-1">
              <span className={axisLabel}>{row.axis}</span>
              <button
                type="button"
                className={btn}
                disabled={busy !== null}
                onClick={() => apply(row.neg90)}
                title={`Rotate -90° around ${row.axis}`}
              >
                -90°
              </button>
              <button
                type="button"
                className={btn}
                disabled={busy !== null}
                onClick={() => apply(row.pos90)}
                title={`Rotate +90° around ${row.axis}`}
              >
                +90°
              </button>
              <button
                type="button"
                className={btn}
                disabled={busy !== null}
                onClick={() => apply(row.half)}
                title={`Rotate 180° around ${row.axis}`}
              >
                180°
              </button>
            </div>
          ))}
        </div>
      </div>
      {err && (
        <div className="text-error text-[11px] px-1.5 py-0.5 rounded bg-error/10 border border-error/30 max-w-xs">
          {err}
        </div>
      )}
    </div>
  );
}
