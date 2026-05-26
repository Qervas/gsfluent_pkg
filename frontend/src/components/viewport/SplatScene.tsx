import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SparkRenderer, SplatMesh, PackedSplats } from "@sparkjsdev/spark";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";
import { splatsGsqUrl } from "@/lib/api";
import { splatArgs, makeSplatArgs } from "@/lib/gsq/splat-writer";
import { useGsqPlayer } from "./use-gsq-player";
import type { GsqFrame, GsqStatic } from "@/lib/gsq";

/** In-browser splat sequence renderer. Replaces the viser iframe for
 *  sequence cells: downloads + decodes the .gsq (worker), renders with Spark,
 *  and advances on the store's currentFrameIdx. Publishes n_frames + the
 *  actually-rendered cursor (pushed_frame) into viserState so the existing
 *  PlaybackBar/Driver keep working (no-skip: pushed_frame trails the splat). */
export function SplatScene() {
  const { activeCell, isSequence } = useActiveCell();
  const name = isSequence ? activeCell?.name ?? null : null;
  const url = name ? splatsGsqUrl(name) : null;

  const currentFrameIdx = useStore((s) => s.currentFrameIdx);
  const setViserState = useStore((s) => s.setViserState);
  const setFpsHint = useStore((s) => s.setFpsHint);

  const mountRef = useRef<HTMLDivElement>(null);

  const glRef = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene;
    camera: THREE.PerspectiveCamera; controls: OrbitControls;
    grid: THREE.GridHelper | null;
  } | null>(null);

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
    const s = useStore.getState().viserState;
    setViserState({ ...s, frame: idx, pushed_frame: idx });
  }

  const player = useGsqPlayer(url, writeFrame);

  // three.js lifecycle (mount once)
  useEffect(() => {
    const mount = mountRef.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, mount.clientWidth / mount.clientHeight, 0.001, 10000);
    camera.up.set(0, 0, 1); // gsfluent data is Z-up
    const controls = new OrbitControls(camera, renderer.domElement);
    scene.add(new SparkRenderer({ renderer }));
    glRef.current = { renderer, scene, camera, controls, grid: null };

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      controls.update();
      renderer.render(scene, camera);
    };
    loop();

    const onResize = () => {
      camera.aspect = mount.clientWidth / mount.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(mount.clientWidth, mount.clientHeight);
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      controls.dispose();
      renderer.dispose();
      mount.removeChild(renderer.domElement);
      glRef.current = null;
    };
  }, []);

  // on static ready: build the SplatMesh + fit camera + request frame 0
  useEffect(() => {
    const gl = glRef.current;
    if (player.status !== "ready" || !player.static || !gl) return;
    const st = player.static;
    staticRef.current = st;
    setFpsHint(st.fpsHint);
    setViserState({ cell: name, frame: 0, n_frames: st.nFrames, pushed_frame: -1 });

    const packed = new PackedSplats({ maxSplats: st.nSplats });
    packedRef.current = packed;
    const mesh = new SplatMesh({ packedSplats: packed });
    meshRef.current = mesh;
    gl.scene.add(mesh);

    const bmin = st.bboxMin, bmax = st.bboxMax;
    const center = new THREE.Vector3(
      (bmin[0] + bmax[0]) / 2, (bmin[1] + bmax[1]) / 2, (bmin[2] + bmax[2]) / 2);
    const extent = Math.max(bmax[0] - bmin[0], bmax[1] - bmin[1], bmax[2] - bmin[2]) || 1;
    const grid = new THREE.GridHelper(extent * 2, 24, 0x888888, 0x333333);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(center.x, center.y, bmin[2]);
    gl.scene.add(grid);
    gl.grid = grid;

    gl.controls.target.copy(center);
    gl.camera.position.set(center.x + extent * 1.2, center.y - extent * 1.2, center.z + extent * 0.8);
    gl.camera.near = extent / 1000;
    gl.camera.far = extent * 100;
    gl.camera.updateProjectionMatrix();
    gl.controls.update();

    player.requestFrame(0);

    return () => {
      if (meshRef.current) { gl.scene.remove(meshRef.current); meshRef.current = null; }
      if (gl.grid) { gl.scene.remove(gl.grid); gl.grid = null; }
      packedRef.current = null;
      staticRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [player.status, player.static, name]);

  // on frame change: request the decode (writeFrame fires on arrival)
  useEffect(() => {
    if (player.status !== "ready") return;
    player.requestFrame(currentFrameIdx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFrameIdx, player.status]);

  return (
    <div ref={mountRef} className="h-full w-full relative bg-canvas">
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
