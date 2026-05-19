/**
 * Browser-side WebGL viewer. Scaffold — full @mkkellogg/gaussian-splats-3d
 * integration lands in Phase 8 follow-up since it needs npz parsing +
 * IndexedDB cell cache, both substantial.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Artifact } from "@/lib/api";

export function ViewerLocal({
  runId, modelId,
}: { runId?: string; modelId?: string }): JSX.Element {
  const enabled = !!runId;
  const arts = useQuery({
    queryKey: ["run", runId, "artifacts"],
    queryFn: () => (runId ? api.runs.artifacts(runId) : Promise.resolve([])),
    enabled,
  });

  const cells = (arts.data ?? []).filter((a: Artifact) => a.kind === "cell");
  const [progress, setProgress] = useState(0);

  // Stubbed download loop — confirms presigned URLs work end-to-end.
  // Real WebGL rasterization wired in follow-up (task #178).
  useEffect(() => {
    if (!cells.length) return;
    let stopped = false;
    setProgress(0);
    (async () => {
      for (let i = 0; i < cells.length && !stopped; i++) {
        try {
          const { url } = await fetch(`/v1/artifacts/${cells[i].id}/url`)
            .then((r) => r.json());
          await fetch(url, { method: "HEAD" });  // just verify reachability
          setProgress(i + 1);
        } catch {
          break;
        }
      }
    })();
    return () => {
      stopped = true;
    };
  }, [cells.map((c) => c.id).join("|")]);

  if (modelId && !runId) {
    return (
      <div className="w-full aspect-video bg-slate-900/60 rounded border border-border grid place-items-center text-slate-400 text-sm">
        Local mode requires a run with `cell` artifacts. Pick a completed run.
      </div>
    );
  }

  return (
    <div className="relative w-full aspect-video bg-slate-900/60 rounded border border-border grid place-items-center">
      <div className="text-center space-y-2">
        <p className="text-slate-300 text-sm">
          Local WebGL viewer — scaffold.
        </p>
        <p className="text-xs text-slate-500">
          {cells.length === 0
            ? "no cells yet"
            : `${progress}/${cells.length} cells fetched`}
        </p>
        <p className="text-xs text-slate-600">
          Real WebGL splat rendering via @mkkellogg/gaussian-splats-3d
          lands in task #178.
        </p>
      </div>
      <div className="absolute top-2 left-2 pill bg-slate-900/80 text-slate-300">
        local · scaffold
      </div>
    </div>
  );
}
