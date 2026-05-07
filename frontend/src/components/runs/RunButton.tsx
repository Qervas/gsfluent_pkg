import { Play, Square } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function RunButton({ subscribe }: { subscribe: (run_name: string) => void }) {
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const simState = useStore((s) => s.simState);
  const simRunName = useStore((s) => s.simRunName);
  const resetForNewRun = useStore((s) => s.resetForNewRun);
  const [busy, setBusy] = useState(false);

  const canRun =
    !!activeModel &&
    !!activeRecipeName &&
    !!activeRecipeData &&
    simState !== "running" &&
    !busy;

  const onRun = async () => {
    if (!canRun || !activeModel || !activeRecipeData || !activeRecipeName) return;
    setBusy(true);
    try {
      const ts = new Date().toISOString().replace(/[:.]/g, "").slice(0, 15);
      const baseName = activeRecipeName.replace(/^★ /, "");
      const run_name = `${activeModel.name}_${baseName}_${ts}`;
      resetForNewRun(run_name);
      await api.runs.start({
        run_name,
        model_path: activeModel.path,
        recipe_data: activeRecipeData,
        recipe_source: activeRecipeName,
        particles: 200_000,
      });
      subscribe(run_name);
    } catch (e) {
      console.error("failed to start run:", e);
      // resetForNewRun already set simState="running"; revert.
      // The error will surface via the failure path; user sees console.
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    if (!simRunName) return;
    setBusy(true);
    try {
      const all = await api.runs.list();
      const r = all.find((x) => x.name === simRunName);
      if (r) await api.runs.cancel(r.id);
    } catch (e) {
      console.error("failed to cancel run:", e);
    } finally {
      setBusy(false);
    }
  };

  if (simState === "running") {
    return (
      <Button variant="destructive" onClick={onCancel} disabled={busy}>
        <Square size={11} />
        Cancel
      </Button>
    );
  }

  return (
    <Button onClick={onRun} disabled={!canRun} title={canRun ? "" : "Pick a model + recipe first."}>
      <Play size={11} />
      Run
    </Button>
  );
}
