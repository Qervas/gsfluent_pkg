/**
 * Browser-side WebGL viewer using @mkkellogg/gaussian-splats-3d.
 *
 * Loads the run's `preview`-kind artifacts (.ply per-frame), caches blobs
 * in IndexedDB, and swaps the active scene based on the PlaybackBar
 * frame index. Falls back to a message if the run has no preview artifacts.
 *
 * .npz artifacts (cell kind) are NOT directly renderable by the splat
 * library; the v1 fuse step writes ply preview frames alongside the npz
 * cells (see engine.py). We render the ply preview here. A future
 * follow-up could ship a chunked SPLAT format to drop the ply step.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Artifact } from "@/lib/api";
import { PlaybackBar, type PlayState } from "./PlaybackBar";
import { StatsHud, type ViewerStats } from "./StatsHud";
import { fetchOrCache } from "@/lib/cellCache";

type ViewerCtor = new (opts: object) => {
  addSplatSceneFromBlob?(blob: Blob): Promise<void>;
  addSplatScene?(url: string): Promise<void>;
  removeSplatScene(index: number): Promise<void>;
  start(): void;
  dispose(): Promise<void>;
};

export function ViewerLocal({
  runId,
}: { runId?: string; modelId?: string }): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<InstanceType<ViewerCtor> | null>(null);
  const sceneCountRef = useRef<number>(0);

  const [play, setPlay] = useState<PlayState>({
    playing: false, frame: 0, total: 0, fps: 10,
  });
  const [loadingMsg, setLoadingMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(0);

  const enabled = !!runId;
  const arts = useQuery({
    queryKey: ["run", runId, "artifacts"],
    queryFn: () => (runId ? api.runs.artifacts(runId) : Promise.resolve([] as Artifact[])),
    enabled,
  });

  const frames = (arts.data ?? [])
    .filter((a) => a.kind === "preview" && a.frame_idx !== null)
    .sort((a, b) => (a.frame_idx ?? 0) - (b.frame_idx ?? 0));

  // Spin up the @mkkellogg viewer once on mount.
  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    (async () => {
      try {
        const mod = await import("@mkkellogg/gaussian-splats-3d");
        if (cancelled) return;
        const Viewer = (mod as unknown as { Viewer: ViewerCtor }).Viewer;
        const v = new Viewer({
          rootElement: containerRef.current,
          selfDrivenMode: true,
          useBuiltInControls: true,
          ignoreDevicePixelRatio: false,
          gpuAcceleratedSort: true,
          enableSIMDInSort: true,
          cameraUp: [0, 1, 0],
          initialCameraPosition: [0, 0, 5],
          initialCameraLookAt: [0, 0, 0],
        });
        viewerRef.current = v;
        v.start();
      } catch (e) {
        setError(`failed to init splat viewer: ${String(e)}`);
      }
    })();

    return () => {
      cancelled = true;
      const v = viewerRef.current;
      viewerRef.current = null;
      if (v) v.dispose().catch(() => {});
    };
  }, []);

  // Whenever the run's frame list changes, reset playback total.
  useEffect(() => {
    setPlay((p) => ({ ...p, total: frames.length, frame: 0 }));
  }, [frames.length]);

  // Swap scenes when the current frame changes.
  useEffect(() => {
    if (!viewerRef.current || frames.length === 0) return;
    const target = frames[play.frame];
    if (!target) return;
    let cancelled = false;

    (async () => {
      setLoadingMsg(`loading frame ${play.frame + 1}/${frames.length}…`);
      try {
        const { url } = await fetch(`/v1/artifacts/${target.id}/url`).then((r) => r.json());
        const blob = await fetchOrCache(target.id, url);
        if (cancelled) return;
        setDownloaded((n) => n + 1);
        const v = viewerRef.current!;

        // Remove all prior scenes (sceneCountRef tracks how many we added).
        for (let i = sceneCountRef.current - 1; i >= 0; i--) {
          await v.removeSplatScene(i);
        }
        sceneCountRef.current = 0;

        if (v.addSplatSceneFromBlob) {
          await v.addSplatSceneFromBlob(blob);
        } else if (v.addSplatScene) {
          const objectUrl = URL.createObjectURL(blob);
          try {
            await v.addSplatScene(objectUrl);
          } finally {
            URL.revokeObjectURL(objectUrl);
          }
        }
        sceneCountRef.current = 1;
        setLoadingMsg(null);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [play.frame, frames]);

  const stats: ViewerStats = {
    mode: "local",
    currentFrame: play.frame,
    frameBytes: frames[play.frame]?.size_bytes,
  };

  return (
    <div className="space-y-2">
      <div className="relative w-full aspect-video bg-black rounded border border-border overflow-hidden">
        <div ref={containerRef} className="w-full h-full" />
        <div className="absolute top-2 left-2 pill bg-slate-900/80 text-slate-300">
          local · {frames.length > 0 ? `${downloaded}/${frames.length} cached` : "no frames"}
        </div>
        {loadingMsg && (
          <div className="absolute bottom-2 left-2 pill bg-slate-900/80 text-slate-400">
            {loadingMsg}
          </div>
        )}
        {error && (
          <div className="absolute inset-x-2 top-10 text-xs text-red-300 bg-red-900/40 p-2 rounded">
            {error}
          </div>
        )}
        {frames.length === 0 && !error && (
          <div className="absolute inset-0 grid place-items-center text-slate-500 text-sm">
            (no preview frames yet — run still computing or only npz cells available)
          </div>
        )}
        <StatsHud stats={stats} />
      </div>
      <PlaybackBar state={play} setState={setPlay} />
    </div>
  );
}
