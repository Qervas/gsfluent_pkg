import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { SparkRenderer } from "@sparkjsdev/spark";

export interface ThreeScene {
  /** Attach to the viewport container div. */
  mountRef: React.RefObject<HTMLDivElement>;
  /** Add/remove a splat (or any) object to the scene. */
  add: (o: THREE.Object3D) => void;
  remove: (o: THREE.Object3D) => void;
  /** Frame the camera + drop a ground grid for a scene of this bbox. */
  frame: (bboxMin: ArrayLike<number>, bboxMax: ArrayLike<number>) => void;
  clearGround: () => void;
  /** Register a callback run once per rAF frame, BEFORE render, with the ms
   *  since the previous frame. Pass null to clear. Drives sequence playback
   *  in lockstep with rendering. */
  setFrameCallback: (cb: ((dtMs: number) => void) | null) => void;
}

/** Owns the raw three.js + Spark render context for the viewport: one canvas,
 *  WebGLRenderer, scene, Z-up camera, OrbitControls, SparkRenderer, ground grid,
 *  rAF loop. Created once on mount, disposed on unmount. Cell-specific content
 *  (splat meshes) is added/removed by the caller via `add`/`remove`. */
export function useThreeScene(): ThreeScene {
  const mountRef = useRef<HTMLDivElement>(null);
  const ctx = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene;
    camera: THREE.PerspectiveCamera; controls: OrbitControls;
    grid: THREE.GridHelper | null;
  } | null>(null);
  const frameCb = useRef<((dtMs: number) => void) | null>(null);

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
    ctx.current = { renderer, scene, camera, controls, grid: null };

    let raf = 0;
    let prev = performance.now();
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const now = performance.now();
      const dt = now - prev;
      prev = now;
      frameCb.current?.(dt);
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
      ctx.current = null;
    };
  }, []);

  return {
    mountRef,
    add: (o) => ctx.current?.scene.add(o),
    remove: (o) => ctx.current?.scene.remove(o),
    clearGround: () => {
      const c = ctx.current;
      if (c?.grid) { c.scene.remove(c.grid); c.grid = null; }
    },
    setFrameCallback: (cb) => { frameCb.current = cb; },
    frame: (bmin, bmax) => {
      const c = ctx.current;
      if (!c) return;
      const center = new THREE.Vector3(
        (bmin[0] + bmax[0]) / 2, (bmin[1] + bmax[1]) / 2, (bmin[2] + bmax[2]) / 2);
      const extent = Math.max(bmax[0] - bmin[0], bmax[1] - bmin[1], bmax[2] - bmin[2]) || 1;
      if (c.grid) { c.scene.remove(c.grid); c.grid = null; }
      const grid = new THREE.GridHelper(extent * 2, 24, 0x888888, 0x333333);
      grid.rotation.x = Math.PI / 2;
      grid.position.set(center.x, center.y, bmin[2]);
      c.scene.add(grid);
      c.grid = grid;
      c.controls.target.copy(center);
      c.camera.position.set(center.x + extent * 1.2, center.y - extent * 1.2, center.z + extent * 0.8);
      c.camera.near = extent / 1000;
      c.camera.far = extent * 100;
      c.camera.updateProjectionMatrix();
      c.controls.update();
    },
  };
}
