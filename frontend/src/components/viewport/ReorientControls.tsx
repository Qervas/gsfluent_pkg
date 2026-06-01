import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useActiveCell } from "@/lib/use-active-cell";

type Transform = "y_up_to_z_up" | "flip_180";

/** Viewport overlay: fix a static model's orientation in place.
 *
 *  The model is overwritten on the server (positions + gaussian quaternions
 *  rotated) and the splat fetch is cache-busted by the new sha256, so the
 *  cloud reloads upright. Repeatable by design — orientation is unknown until
 *  you see it, so apply, look, apply again (Y-up→Z-up ×4 and Flip ×2 are
 *  identities). Only shown for model cells, not sequence playback. */
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

  const btn =
    "px-2.5 py-1 text-xs rounded-md bg-elevated/80 backdrop-blur border border-white/10 " +
    "text-text-secondary hover:text-text-primary hover:border-white/20 transition-colors " +
    "disabled:opacity-50 disabled:cursor-not-allowed";

  return (
    <div className="absolute top-3 left-3 z-10 flex flex-col gap-1.5 items-start">
      <div className="flex gap-2">
        <button
          type="button"
          className={btn}
          disabled={busy !== null}
          onClick={() => apply("y_up_to_z_up")}
          title="Rotate the model Y-up → Z-up (stand a lying-down scan upright). Repeatable."
        >
          {busy === "y_up_to_z_up" ? "rotating…" : "Y-up→Z-up"}
        </button>
        <button
          type="button"
          className={btn}
          disabled={busy !== null}
          onClick={() => apply("flip_180")}
          title="Flip 180° (fix an upside-down model). Repeatable."
        >
          {busy === "flip_180" ? "flipping…" : "Flip 180°"}
        </button>
      </div>
      {err && (
        <div className="text-error text-[11px] px-1.5 py-0.5 rounded bg-error/10 border border-error/30 max-w-xs">
          {err}
        </div>
      )}
    </div>
  );
}
