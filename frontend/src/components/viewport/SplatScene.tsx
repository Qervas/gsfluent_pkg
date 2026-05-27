import { useEffect, useRef } from "react";
import * as THREE from "three";
import { SplatMesh, PackedSplats, SplatFileType } from "@sparkjsdev/spark";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";
import { useQuery } from "@tanstack/react-query";
import { api, splatsGsqUrl, modelPlyUrl } from "@/lib/api";
import { recenterPly } from "@/lib/ply-recenter";
import { splatArgs, makeSplatArgs } from "@/lib/gsq/splat-writer";
import { useGsqPlayer } from "./use-gsq-player";
import { useThreeScene } from "./use-three-scene";
import type { GsqFrame, GsqStatic } from "@/lib/gsq";

/** In-browser splat renderer for the active cell. Sequence cells stream the
 *  .gsq (worker) and animate via per-frame setSplat. Publishes n_frames + the
 *  rendered cursor into playbackState so PlaybackBar/Driver work (no-skip:
 *  pushed_frame set after the GPU write). */
export function SplatScene() {
  const { activeCell, isSequence, isModel } = useActiveCell();
  const { data: models = [] } = useQuery({ queryKey: ["models"], queryFn: api.models.list });
  const modelPath = isModel ? (models.find((m) => m.name === activeCell?.name)?.path ?? null) : null;
  const name = isSequence ? activeCell?.name ?? null : null;
  const url = name ? splatsGsqUrl(name) : null;

  const currentFrameIdx = useStore((s) => s.currentFrameIdx);
  const setPlaybackState = useStore((s) => s.setPlaybackState);
  const setFpsHint = useStore((s) => s.setFpsHint);

  const scene = useThreeScene();

  const packedRef = useRef<PackedSplats | null>(null);
  const meshRef = useRef<SplatMesh | null>(null);
  const staticRef = useRef<GsqStatic | null>(null);
  const argRef = useRef(makeSplatArgs());
  const vC = useRef(new THREE.Vector3());
  const vS = useRef(new THREE.Vector3());
  const vQ = useRef(new THREE.Quaternion());
  const vCol = useRef(new THREE.Color());

  function writeFrame(idx: number, frame: GsqFrame) {
    const st = staticRef.current;
    const packed = packedRef.current;
    if (!st || !packed) return;
    const a = argRef.current;
    for (let i = 0; i < st.nSplats; i++) {
      splatArgs(frame, st, i, a);
      vC.current.set(a.center[0], a.center[1], a.center[2]);
      vS.current.set(a.scales[0], a.scales[1], a.scales[2]);
      vQ.current.set(a.quat[0], a.quat[1], a.quat[2], a.quat[3]);
      vCol.current.setRGB(a.color[0], a.color[1], a.color[2]);
      packed.setSplat(i, vC.current, vS.current, vQ.current, a.opacity, vCol.current);
    }
    packed.numSplats = st.nSplats;
    packed.needsUpdate = true;
    const s = useStore.getState().playbackState;
    setPlaybackState({ ...s, frame: idx, pushed_frame: idx });
  }

  const player = useGsqPlayer(url, writeFrame);

  useEffect(() => {
    if (player.status !== "ready" || !player.static) return;
    const st = player.static;
    staticRef.current = st;
    setFpsHint(st.fpsHint);
    setPlaybackState({ cell: name, frame: 0, n_frames: st.nFrames, pushed_frame: -1 });

    const packed = new PackedSplats({ maxSplats: st.nSplats });
    packedRef.current = packed;
    const mesh = new SplatMesh({ packedSplats: packed });
    meshRef.current = mesh;
    scene.add(mesh);
    scene.frame(st.bboxMin, st.bboxMax);
    player.requestFrame(0);

    return () => {
      if (meshRef.current) { scene.remove(meshRef.current); meshRef.current = null; }
      scene.clearGround();
      packedRef.current = null;
      staticRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [player.status, player.static, name]);

  useEffect(() => {
    if (player.status !== "ready") return;
    player.requestFrame(currentFrameIdx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFrameIdx, player.status]);

  // model cell: load the static .ply via Spark (no worker, no animation).
  // We fetch + recenter the .ply to the origin BEFORE handing bytes to Spark:
  // our scans live at world coords ~29000 where Spark's float16 splat centers
  // collapse to a couple of planes (see recenterPly). On any parse failure we
  // fall back to letting Spark fetch the raw url itself.
  useEffect(() => {
    if (!isModel || !modelPath) return;
    let alive = true;
    let mesh: SplatMesh | null = null;
    setPlaybackState({ cell: activeCell?.name ?? null, frame: 0, n_frames: 0, pushed_frame: 0 });

    const fitFrom = (m: SplatMesh) => {
      const box = m.getBoundingBox();
      if (box.isEmpty()) scene.frame([-1, -1, -1], [1, 1, 1]);
      else scene.frame(box.min.toArray(), box.max.toArray());
    };

    void (async () => {
      const url = modelPlyUrl(modelPath);
      try {
        const buf = await fetch(url).then((r) => {
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
        mesh = new SplatMesh({ url });
        scene.add(mesh);
        void mesh.initialized.then((m) => { if (alive) fitFrom(m); });
      }
    })();

    return () => { alive = false; if (mesh) scene.remove(mesh); scene.clearGround(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isModel, modelPath, activeCell?.name]);

  return (
    <div ref={scene.mountRef} className="h-full w-full relative bg-canvas">
      {player.status === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center text-text-muted text-sm pointer-events-none">
          downloading splats… {player.total ? `${((player.progress / player.total) * 100).toFixed(0)}%` : `${(player.progress / 1e6).toFixed(1)} MB`}
        </div>
      )}
      {player.status === "error" && (
        <div className="absolute inset-0 flex items-center justify-center text-error text-sm">
          decode error: {player.error}
        </div>
      )}
    </div>
  );
}
