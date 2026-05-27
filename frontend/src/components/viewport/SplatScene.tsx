import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { SplatMesh, PackedSplats, SplatFileType } from "@sparkjsdev/spark";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";
import { useQuery } from "@tanstack/react-query";
import { api, splatsGsqUrl, modelPlyUrl } from "@/lib/api";
import { recenterPly } from "@/lib/ply-recenter";
import { splatArgs, makeSplatArgs } from "@/lib/gsq/splat-writer";
import { GsqDecoder } from "@/lib/gsq/decoder";
import { downloadGsq } from "@/lib/gsq/download";
import { tickPlayback } from "@/lib/playback";
import { useThreeScene } from "./use-three-scene";

type SeqStatus = "idle" | "waiting" | "building" | "loading" | "ready" | "error";

const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, ms));

/** In-browser splat renderer for the active cell.
 *
 *  Sequence cells download the .gsq once and animate it entirely inside the
 *  shared rAF loop (see useThreeScene.setFrameCallback): a wall-clock
 *  accumulator advances at most one frame per tick — never skipping — and
 *  decodes synchronously via GsqDecoder's sequential cache (frame N->N+1 is a
 *  single delta step), then writes the splats right before render. No worker,
 *  no per-frame React state — that two-clock pipeline was the playback stutter.
 *  React carries only coarse intent (playing / loop / reset). Mirrors the
 *  proven-smooth spike-spark/src/main.ts.
 *
 *  Model cells render a single static .ply via Spark (recentered first — see
 *  the model effect below). */
export function SplatScene() {
  const { activeCell, isSequence, isModel } = useActiveCell();
  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const modelPath = isModel ? (models.find((m) => m.name === activeCell?.name)?.path ?? null) : null;
  const name = isSequence ? activeCell?.name ?? null : null;
  const url = name ? splatsGsqUrl(name) : null;

  // Shared with App.tsx (react-query dedupes by key) — gives per-sequence
  // frame_count, refetched every 5s. Sim frames materialize all at once after
  // the fuse stage, so frame_count >= 1 is the reliable "ready to pack" signal
  // (independent of the run-log done markers, which can be stale).
  const { data: sequences = [] } = useQuery({
    queryKey: ["sequences"],
    queryFn: api.sequences.list,
    refetchInterval: 5_000,
  });
  const framesReady =
    !!name && (sequences.find((s) => s.name === name)?.frame_count ?? 0) >= 1;
  const simState = useStore((s) => s.simState);

  const setPlaybackState = useStore((s) => s.setPlaybackState);

  const scene = useThreeScene();

  // THREE temporaries reused across the per-frame setSplat loop.
  const vC = useRef(new THREE.Vector3());
  const vS = useRef(new THREE.Vector3());
  const vQ = useRef(new THREE.Quaternion());
  const vCol = useRef(new THREE.Color());
  const argRef = useRef(makeSplatArgs());

  const [seqStatus, setSeqStatus] = useState<SeqStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [total, setTotal] = useState<number | null>(null);
  const [seqError, setSeqError] = useState<string | null>(null);

  // Sequence playback. Pipeline:
  //   1. wait until the sim has produced frames (frame_count >= 1)
  //   2. ensure the server-side .gsq cache is built — POST /cache/build
  //      (idempotent: instant "done" if it already exists) then poll
  //      /cache/build-status until done/error. This was the missing wiring:
  //      the old build orchestration lived in ViserSplatScene and was dropped
  //      in the viser purge, so a freshly-run sequence 404'd forever.
  //   3. download the .gsq once, decode + animate in-browser inside the
  //      shared rAF loop (decoder's sequential cache => one delta step/frame).
  useEffect(() => {
    if (!isSequence || !url || !name) return;
    let alive = true;
    let mesh: SplatMesh | null = null;
    let packed: PackedSplats | null = null;
    let decoder: GsqDecoder | null = null;

    setSeqError(null);
    if (!framesReady) {
      // No frames yet — sim still running, or it failed to produce any.
      setSeqStatus("waiting");
      return () => { alive = false; scene.setFrameCallback(null); };
    }
    setProgress(0);
    setTotal(null);

    const writeFrame = (idx: number) => {
      if (!decoder || !packed) return;
      const st = decoder.static;
      const f = decoder.decodeFrame(idx);
      const a = argRef.current;
      for (let i = 0; i < st.nSplats; i++) {
        splatArgs(f, st, i, a);
        vC.current.set(a.center[0], a.center[1], a.center[2]);
        vS.current.set(a.scales[0], a.scales[1], a.scales[2]);
        vQ.current.set(a.quat[0], a.quat[1], a.quat[2], a.quat[3]);
        vCol.current.setRGB(a.color[0], a.color[1], a.color[2]);
        packed.setSplat(i, vC.current, vS.current, vQ.current, a.opacity, vCol.current);
      }
      packed.numSplats = st.nSplats;
      packed.needsUpdate = true;
    };

    void (async () => {
      try {
        // 1. Ensure the .gsq is packed server-side (idempotent).
        setSeqStatus("building");
        let job = await api.sequences.buildCache(name);
        for (let i = 0; alive && job.state === "building" && i < 310; i++) {
          await sleep(2000); // builds run ~30s-10min; server caps at 600s
          if (!alive) return;
          job = await api.sequences.buildStatus(name);
        }
        if (!alive) return;
        if (job.state === "error") throw new Error(job.error || "cache build failed");
        if (job.state !== "done") throw new Error("cache build timed out");

        // 2. Download the packed .gsq once.
        setSeqStatus("loading");
        const buf = await downloadGsq(url, (p) => {
          if (!alive) return;
          setProgress(p.received);
          setTotal(p.total);
        });
        if (!alive) return;
        decoder = new GsqDecoder(buf);
        const st = decoder.static;
        packed = new PackedSplats({ maxSplats: st.nSplats });
        mesh = new SplatMesh({ packedSplats: packed });
        scene.add(mesh);
        scene.frame(st.bboxMin, st.bboxMax);
        setPlaybackState({ n_frames: st.nFrames });
        writeFrame(0);
        setSeqStatus("ready");

        // Drive playback in lockstep with rendering. Locals persist for the
        // effect's lifetime (like the spike's loop vars). Coarse intent
        // (playing / loop / resetNonce) is read fresh from the store each tick.
        let frame = 0;
        let acc = 0;
        const interval = 1000 / Math.max(st.fpsHint, 1); // file's native rate
        let lastNonce = useStore.getState().resetNonce;

        scene.setFrameCallback((dt) => {
          const s = useStore.getState();
          if (s.resetNonce !== lastNonce) {
            lastNonce = s.resetNonce;
            frame = 0;
            acc = 0;
            writeFrame(0);
            return;
          }
          const r = tickPlayback(frame, acc, dt, interval, st.nFrames, s.playing, s.loop);
          acc = r.acc;
          if (r.stopped && s.playing) s.setPlaying(false);
          if (r.advanced) { frame = r.frame; writeFrame(frame); }
        });
      } catch (err) {
        if (!alive) return;
        setSeqError(err instanceof Error ? err.message : String(err));
        setSeqStatus("error");
      }
    })();

    return () => {
      alive = false;
      scene.setFrameCallback(null);
      if (mesh) scene.remove(mesh);
      scene.clearGround();
      mesh = null;
      packed = null;
      decoder = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSequence, url, name, framesReady]);

  // model cell: load the static .ply via Spark (no playback loop).
  // We fetch + recenter the .ply to the origin BEFORE handing bytes to Spark:
  // our scans live at world coords ~29000 where Spark's float16 splat centers
  // collapse to a couple of planes (see recenterPly). On any parse failure we
  // fall back to letting Spark fetch the raw url itself.
  useEffect(() => {
    if (!isModel || !modelPath) return;
    let alive = true;
    let mesh: SplatMesh | null = null;
    scene.setFrameCallback(null); // static model: no playback loop
    setPlaybackState({ n_frames: 0 });

    const fitFrom = (m: SplatMesh) => {
      const box = m.getBoundingBox();
      if (box.isEmpty()) scene.frame([-1, -1, -1], [1, 1, 1]);
      else scene.frame(box.min.toArray(), box.max.toArray());
    };

    void (async () => {
      const u = modelPlyUrl(modelPath);
      try {
        const buf = await fetch(u).then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.arrayBuffer();
        });
        if (!alive) return;
        const { bytes, min, max } = recenterPly(buf);
        mesh = new SplatMesh({ fileBytes: bytes, fileType: SplatFileType.PLY });
        scene.add(mesh);
        scene.frame(min, max); // recentered bounds — frame immediately
        void mesh.initialized.then((m) => { if (alive) fitFrom(m); });
      } catch (err) {
        // Recenter/fetch failed — let Spark load the raw url (precision may suffer).
        console.warn("ply recenter failed, falling back to url:", err);
        if (!alive) return;
        mesh = new SplatMesh({ url: u });
        scene.add(mesh);
        void mesh.initialized.then((m) => { if (alive) fitFrom(m); });
      }
    })();

    return () => { alive = false; if (mesh) scene.remove(mesh); scene.clearGround(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isModel, modelPath, activeCell?.name]);

  return (
    <div ref={scene.mountRef} className="h-full w-full relative bg-canvas">
      {seqStatus === "waiting" && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          {simState === "error"
            ? "simulation failed — no frames produced (check the run log)"
            : simState === "running"
              ? "simulation running — waiting for frames…"
              : "waiting for simulation frames…"}
        </div>
      )}
      {seqStatus === "building" && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          preparing splats (building cache on server)…
        </div>
      )}
      {seqStatus === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          downloading splats… {total ? `${((progress / total) * 100).toFixed(0)}%` : `${(progress / 1e6).toFixed(1)} MB`}
        </div>
      )}
      {seqStatus === "error" && (
        <div className="absolute inset-0 flex items-center justify-center text-error text-sm px-6 text-center">
          {seqError}
        </div>
      )}
    </div>
  );
}
