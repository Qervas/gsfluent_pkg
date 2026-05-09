import { useEffect, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Viewer, SceneFormat } from "@mkkellogg/gaussian-splats-3d";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { useStore } from "@/lib/store";

/**
 * Proper 3D Gaussian Splatting render path. Two source modes:
 *
 *   1. STATIC MODEL: bootstrap from /api/models/file/<name>.ply, render
 *      anisotropic ellipsoids forever (the original splat path).
 *
 *   2. SIM RUN: bootstrap from /api/runs/<run>/frame/0.ply (full attrs),
 *      then watch the WS-streamed frameXyz Map and write each new frame's
 *      positions into the splat mesh's centers data texture in-place. The
 *      lib's GPU sort is forced to refresh after each upload so depth
 *      ordering tracks the new geometry.
 *
 * Coordinate system: COLMAP-native (Y-up), no per-mesh rotation. Earlier
 * Z-up rotation attempts caused silent invisibility — this matches the
 * working splat-test.html config. Viewport flips R3F camera.up alongside.
 */
export function GaussianSplatScene() {
  const activeModel = useStore((s) => s.activeModel);
  const simRunName = useStore((s) => s.simRunName);
  const setSceneScale = useStore((s) => s.setSceneScale);
  const setSceneFloor = useStore((s) => s.setSceneFloor);
  const { gl, scene, camera, controls } = useThree() as unknown as {
    gl: THREE.WebGLRenderer;
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControlsImpl | null;
  };

  const viewerRef = useRef<any>(null);
  const [splatMesh, setSplatMesh] = useState<THREE.Object3D | null>(null);

  // Decide source: sim run takes priority over model preview when both are
  // set (a sim is always associated with the model it was started from).
  const isSimRun =
    typeof simRunName === "string" &&
    simRunName.length > 0 &&
    !simRunName.startsWith("_model:");

  // For sim runs, wait for the WS pump to have parsed frame_0 before
  // bootstrapping the splat lib — that's the signal that frame_0.ply
  // actually exists on disk and the HTTP endpoint will serve it. Without
  // this gate, live runs would hit `addSplatScene` against a 404 because
  // the sim hasn't produced any frames yet.
  const staticAttrsReady = useStore((s) => s.staticAttrs !== null);

  useEffect(() => {
    // Either sim-run mode (need run name + WS bootstrap signal) or static
    // model mode (need model). Sim mode also needs the first frame on disk,
    // signaled by static_attrs being populated by the WS pump.
    if (!isSimRun && !activeModel) return;
    if (isSimRun && !staticAttrsReady) return;

    const viewer = new Viewer({
      dropInMode: true,
      selfDrivenMode: false,
      useBuiltInControls: false,
      rootElement: null,
      sharedMemoryForWorkers: false,
      renderer: gl,
      camera,
    });
    viewerRef.current = viewer;

    let cancelled = false;
    const url = isSimRun
      ? `/api/runs/${encodeURIComponent(simRunName!)}/frame/0.ply`
      : `/api/models/file/${encodeURIComponent(activeModel!.name)}.ply?path=${encodeURIComponent(activeModel!.path)}`;

    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.log("[GaussianSplatScene] loading", { isSimRun, url });
    }

    viewer
      .addSplatScene(url, {
        format: SceneFormat.Ply,
        showLoadingUI: false,
      })
      .then(() => {
        if (cancelled) return;
        const mesh = viewer.splatMesh;
        if (!mesh) return;
        mesh.frustumCulled = false;

        // Sample splat centers (raw, COLMAP coords) for camera framing.
        const n = mesh.getSplatCount();
        const bbox = new THREE.Box3();
        const c = new THREE.Vector3();
        const stride = Math.max(1, Math.floor(n / 5000));
        for (let i = 0; i < n; i += stride) {
          mesh.getSplatCenter(i, c);
          bbox.expandByPoint(c);
        }
        if (bbox.isEmpty()) return;

        const center = new THREE.Vector3();
        const size = new THREE.Vector3();
        bbox.getCenter(center);
        bbox.getSize(size);
        const diag = size.length() || 1;

        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.log("[GaussianSplatScene] LOADED", {
            mode: isSimRun ? "sim" : "model",
            n,
            center: center.toArray(),
            size: size.toArray(),
            diag,
          });
        }

        // Camera framing. Up-axis is owned by <UpAxisSync> in Viewport.
        camera.position.copy(
          center.clone().add(new THREE.Vector3(diag, diag * 0.5, diag)),
        );
        camera.near = Math.max(diag * 0.001, 0.01);
        camera.far = Math.max(diag * 100, camera.position.length() * 2);
        camera.updateProjectionMatrix();
        camera.lookAt(center);
        if (controls?.target?.copy) {
          controls.target.copy(center);
          controls.update?.();
        }
        setSceneScale(diag, [center.x, center.y, center.z]);
        setSceneFloor(bbox.min.y);

        setSplatMesh(mesh);
      })
      .catch((err: unknown) => {
        // eslint-disable-next-line no-console
        console.error("[GaussianSplatScene] addSplatScene failed:", err);
      });

    return () => {
      cancelled = true;
      try {
        viewer.dispose?.();
      } catch {
        /* dispose can throw mid-load */
      }
      viewerRef.current = null;
      setSplatMesh(null);
    };
    // Re-init when the source identity changes — a different sim run or a
    // different model means a different first-frame ply to bootstrap from.
  }, [isSimRun, simRunName, activeModel, staticAttrsReady, gl, camera, controls, setSceneScale, setSceneFloor]);

  useEffect(() => {
    if (!splatMesh) return;
    scene.add(splatMesh);
    return () => {
      scene.remove(splatMesh);
    };
  }, [splatMesh, scene]);

  // ---- Sim-mode per-frame center updates ---------------------------------
  // Watch the WS-streamed frameXyz Map and the playback head; on each
  // tick, push the current frame's xyz into the splat mesh's centers
  // data texture and force a sort refresh.
  const lastWrittenFrame = useRef<number>(-1);
  const warnedLengthMismatch = useRef<boolean>(false);
  useFrame(() => {
    const v = viewerRef.current;
    if (!v) return;

    // Sim mode: stream new frames into the splat mesh.
    if (isSimRun && splatMesh) {
      const st = useStore.getState();
      const idx = st.currentFrameIdx;
      if (idx !== lastWrittenFrame.current) {
        const xyz = st.frameXyz.get(idx);
        // Lib internals — keep guarded so a future lib upgrade that
        // renames these fields fails loudly rather than silently.
        const sm: any = splatMesh;
        const baseCenters: Float32Array | undefined =
          sm.splatDataTextures?.baseData?.centers;
        if (xyz && baseCenters && xyz.length === baseCenters.length) {
          baseCenters.set(xyz);
          // Re-upload centers texture from baseData.
          try {
            sm.updateDataTexturesFromBaseData(0, sm.getSplatCount() - 1);
            // Force a fresh GPU depth sort against the new positions.
            v.runSplatSort?.(true, true);
            lastWrittenFrame.current = idx;
          } catch (e) {
            // eslint-disable-next-line no-console
            console.warn("[GaussianSplatScene] per-frame update failed:", e);
          }
        } else if (xyz && baseCenters && !warnedLengthMismatch.current) {
          // Sim emits a different particle count than the bootstrap .ply
          // (likely interior particles from filling vs. original gaussian
          // centers). The animation is silently frozen at frame 0 — surface
          // the cause once so the user knows to fall back to Points mode.
          warnedLengthMismatch.current = true;
          // eslint-disable-next-line no-console
          console.warn(
            `[GaussianSplatScene] frame xyz length (${xyz.length}) does not match splat centers (${baseCenters.length}); per-frame splat update disabled — switch to Points mode for sim playback`,
          );
        }
      }
    }

    if (splatMesh) splatMesh.updateMatrixWorld(true);
    try {
      v.update(gl, camera);
    } catch {
      /* lib may throw on first few frames before init completes */
    }
  });

  return null;
}
