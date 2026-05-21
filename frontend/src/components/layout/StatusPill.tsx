import { useState } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useDiag } from "@/lib/use-diag";
import type { DiagPart } from "@/lib/types";

/** Three-dot diagnostics pill for the top bar.
 *
 * One dot per moving part of the split-topology stack: server backend
 * (reached via the vite proxy), the client sync_daemon (via
 * viser_headless's pass-through), and viser_headless itself. Green
 * means the last poll succeeded; red means the last poll failed or
 * the component reports offline. Hover for one-line details per part.
 *
 * Why dots not labels? Three labels in the top bar would compete with
 * the model/recipe name + run state pill that already live there.
 * Dots stay invisible until something goes red, which is when the
 * user actually needs them.
 */
export function StatusPill() {
  const diag = useDiag();
  const [open, setOpen] = useState(false);

  // Sync daemon dot is irrelevant in the server-only deployment — everything
  // (sim, npz cache, viser, UI) runs on the same host, so no client/server
  // download to monitor. Drop it from the pill to stop showing a permanently-
  // yellow indicator that just confuses the user.
  const parts: { key: string; label: string; part: DiagPart }[] = [
    { key: "backend", label: "Backend", part: diag.backend },
    { key: "viser",   label: "Viser",   part: diag.viser   },
  ];
  const anyDown = parts.some((p) => !p.part.ok);

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip open={open} onOpenChange={setOpen}>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={anyDown ? "Diagnostics — issue detected" : "Diagnostics — all systems ok"}
            className={
              "inline-flex items-center gap-1 px-1.5 py-0.5 rounded border " +
              (anyDown
                ? "border-red-500/60 bg-red-500/10"
                : "border-border bg-elevated/40 hover:bg-elevated/70")
            }
            onClick={() => setOpen((o) => !o)}
          >
            {parts.map((p) => (
              <span
                key={p.key}
                className={
                  "block w-1.5 h-1.5 rounded-full " +
                  (p.part.ok ? "bg-emerald-400" : "bg-red-500")
                }
                aria-label={`${p.label}: ${p.part.ok ? "ok" : "down"}`}
              />
            ))}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" align="end" className="p-2 min-w-[220px]">
          <div className="flex flex-col gap-1.5 font-mono">
            {parts.map((p) => (
              <div key={p.key} className="flex items-start gap-2">
                <span
                  className={
                    "mt-1 block w-1.5 h-1.5 rounded-full shrink-0 " +
                    (p.part.ok ? "bg-emerald-400" : "bg-red-500")
                  }
                />
                <div className="flex flex-col leading-tight">
                  <span className="text-text-primary">{p.label}</span>
                  <span className="text-[10px] text-text-muted">
                    {p.part.ok
                      ? (p.part.detail ?? "ok")
                      : (p.part.error ?? "down")}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
