import { Upload, Check } from "lucide-react";
import { useStore } from "@/lib/store";

/** Onboarding guide rendered over the empty viewport. Walks first-time
 *  users through the three-step path to a working sim:
 *
 *    1. Load a 3DGS model (drag-drop a .ply or pick one in the Outliner)
 *    2. Pick a recipe (Outliner → Recipes section)
 *    3. ▶ Run
 *
 *  Each step ticks ✓ as state advances. Steps users have already
 *  completed are dimmed; the next-action step is highlighted with the
 *  accent color. The whole card auto-hides after the user fires their
 *  first successful sim run (handled by AppShell: this component is
 *  only mounted when no model is loaded AND no sim is running).
 */
export function EmptyState() {
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);

  const steps = [
    {
      n: 1,
      label: "Load a 3DGS model",
      hint: "drag a .ply onto the viewport, or pick one in the Outliner",
      done: !!activeModel,
    },
    {
      n: 2,
      label: "Pick a recipe",
      hint: "Outliner → Recipes section, or paste your own under Recipes tab",
      done: !!activeRecipeName,
    },
    {
      n: 3,
      label: "Click Run",
      hint: "top-right corner. The Run button lights up once 1 + 2 are done.",
      done: false,
    },
  ];

  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
      <div
        className="glass-card pointer-events-auto px-6 py-5 max-w-md w-[420px]
                   shadow-glass"
      >
        <div className="flex items-center gap-3 mb-4">
          <Upload size={20} className="text-accent" />
          <div>
            <div className="text-base font-semibold text-text-primary">
              Welcome to gsfluent
            </div>
            <div className="text-xxs uppercase tracking-wider text-text-muted mt-0.5">
              3 steps to your first sim
            </div>
          </div>
        </div>

        <ol className="space-y-2.5">
          {steps.map((s, i) => {
            // The "active" step is the first one that isn't done — it's
            // the user's next move. Earlier steps are checked off; later
            // ones are dimmed.
            const isActive = !s.done && steps.slice(0, i).every((p) => p.done);
            return (
              <li
                key={s.n}
                className={
                  "flex items-start gap-3 p-2 rounded-md transition-colors duration-fast " +
                  (isActive ? "bg-accent/8" : "")
                }
              >
                <span
                  className={
                    "shrink-0 w-6 h-6 rounded-full flex items-center justify-center " +
                    "text-xs font-mono font-semibold " +
                    (s.done
                      ? "bg-success/20 text-success"
                      : isActive
                      ? "bg-accent text-canvas shadow-accent-glow-soft"
                      : "bg-elevated text-text-muted border border-border/50")
                  }
                  aria-hidden
                >
                  {s.done ? <Check size={12} /> : s.n}
                </span>
                <div className="flex-1 min-w-0">
                  <div
                    className={
                      "text-sm font-medium " +
                      (s.done
                        ? "text-text-muted line-through"
                        : isActive
                        ? "text-text-primary"
                        : "text-text-secondary")
                    }
                  >
                    {s.label}
                  </div>
                  <div className="text-xxs text-text-muted mt-0.5 leading-snug">
                    {s.hint}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}
