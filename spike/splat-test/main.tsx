import { createRoot } from "react-dom/client";
import React, { useEffect, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as GaussianSplats3D from "@mkkellogg/gaussian-splats-3d";
import * as THREE from "three";

const N = 200_000;

/**
 * Build an UncompressedSplatArray of N torus splats, then bake into a SplatBuffer.
 * SH degree = 0 (no view-dependent color), compressionLevel = 0 (keeps base centers
 * as a plain Float32Array we can mutate later via splatMesh.splatDataTextures.baseData.centers).
 */
function buildSplatBuffer(n: number) {
  const arr = new GaussianSplats3D.UncompressedSplatArray(0);
  for (let i = 0; i < n; i++) {
    const u = Math.random() * Math.PI * 2;
    const v = Math.random() * Math.PI * 2;
    const x = (1 + 0.4 * Math.cos(v)) * Math.cos(u);
    const y = 0.4 * Math.sin(v);
    const z = (1 + 0.4 * Math.cos(v)) * Math.sin(u);
    const s = 0.005;
    // qw, qx, qy, qz — identity. The lib stores quat as (rot0..rot3); first slot is w.
    const r = ((x * 0.5 + 0.5) * 255) | 0;
    const g = 128;
    const b = ((z * 0.5 + 0.5) * 255) | 0;
    const a = 200;
    arr.addSplatFromComonents(x, y, z, s, s, s, 1, 0, 0, 0, r, g, b, a);
  }
  const gen = GaussianSplats3D.SplatBufferGenerator.getStandardGenerator(
    0, // alphaRemovalThreshold — keep all splats
    0, // compressionLevel = 0 (uncompressed)
    0, // sectionSize = 0 (auto)
    new THREE.Vector3()
  );
  return gen.generateFromUncompressedSplatArray(arr);
}

function App() {
  const [fps, setFps] = useState(0);
  const [status, setStatus] = useState("loading splats...");
  return (
    <div style={{ height: "100vh", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          zIndex: 10,
          padding: 8,
          fontSize: 13,
          lineHeight: 1.5,
        }}
      >
        N={N.toLocaleString()} splats — live `centers` updates per frame
        <br />
        fps={fps.toFixed(1)} — {status}
        <br />
        drag to orbit; scroll to zoom
      </div>
      <Canvas camera={{ position: [0, 1.5, 3.5], fov: 50 }} dpr={1}>
        <SplatScene
          n={N}
          onFps={setFps}
          onStatus={setStatus}
        />
        <OrbitControls />
      </Canvas>
    </div>
  );
}

function SplatScene({
  n,
  onFps,
  onStatus,
}: {
  n: number;
  onFps: (f: number) => void;
  onStatus: (s: string) => void;
}) {
  // Lazy viewer ownership: the effect creates the DropInViewer, registers it
  // here for useFrame, and disposes it on cleanup. We mirror it into state so
  // <primitive> swaps from the placeholder Group to the real viewer once mounted.
  const viewerRef = useRef<any>(null);
  const baseCentersRef = useRef<Float32Array | null>(null);
  const readyRef = useRef(false);
  const lastFpsT = useRef(performance.now());
  const frames = useRef(0);
  const [viewer, setViewer] = useState<any>(null);

  useEffect(() => {
    let cancelled = false;
    const v = new GaussianSplats3D.DropInViewer({
      gpuAcceleratedSort: true,
      sharedMemoryForWorkers: false,
      dynamicScene: true,
      sphericalHarmonicsDegree: 0,
      antialiased: false,
      logLevel: 0,
    });
    viewerRef.current = v;
    baseCentersRef.current = new Float32Array(n * 3);
    readyRef.current = false;
    setViewer(v);

    onStatus("baking splat buffer...");
    const t0 = performance.now();
    const buf = buildSplatBuffer(n);
    const tBake = performance.now() - t0;
    if (!cancelled) onStatus(`bake=${tBake.toFixed(0)}ms; uploading...`);

    // viewer.addSplatBuffers(buffers, options[], finalBuild, showLoadingUI,
    //                        showLoadingUIForSplatTreeBuild, replaceExisting,
    //                        enableRenderBeforeFirstSort, preserveVisibleRegion)
    v.viewer
      .addSplatBuffers(
        [buf],
        [{}],
        true, // finalBuild
        false, // showLoadingUI
        false, // showLoadingUIForSplatTreeBuild
        false, // replaceExisting
        true, // enableRenderBeforeFirstSort — start drawing immediately
        true // preserveVisibleRegion
      )
      .then(() => {
        if (cancelled) return;
        const sm = v.splatMesh;
        if (!sm || !sm.splatDataTextures?.baseData?.centers) {
          onStatus(
            "FAIL: splatMesh.splatDataTextures.baseData.centers not exposed"
          );
          return;
        }
        // Snapshot the baked centers so we can drive a sin-wave offset off them.
        baseCentersRef.current!.set(sm.splatDataTextures.baseData.centers);
        readyRef.current = true;
        onStatus(`ready — bake=${tBake.toFixed(0)}ms; mutating per frame`);
      })
      .catch((e: any) => {
        if (cancelled) return;
        onStatus(`addSplatBuffers failed: ${e?.message ?? e}`);
      });

    return () => {
      cancelled = true;
      readyRef.current = false;
      // Fire-and-forget dispose; under StrictMode the first cleanup tears down
      // the first viewer before the second mount creates a fresh one. The lib's
      // dispose() returns a Promise that may reject if called mid-load — swallow.
      v.dispose?.().catch(() => {});
      if (viewerRef.current === v) viewerRef.current = null;
    };
  }, [n, onStatus]);

  useFrame(() => {
    const drop = viewerRef.current;
    const base = baseCentersRef.current;
    if (drop && base && readyRef.current) {
      const sm = drop.splatMesh;
      if (sm && sm.splatDataTextures?.baseData?.centers) {
        const centers: Float32Array = sm.splatDataTextures.baseData.centers;
        const t = performance.now() * 0.001;
        // In-place mutate Y axis only.
        for (let i = 0; i < n; i++) {
          const i3 = i * 3;
          centers[i3] = base[i3];
          centers[i3 + 1] = base[i3 + 1] + 0.05 * Math.sin(t * 2 + i * 0.001);
          centers[i3 + 2] = base[i3 + 2];
        }
        // Push centers (and re-pack RGBA padded texture) to the GPU. This is the
        // documented internal path; the lib has no `updateCenters()` public method.
        // With gpuAcceleratedSort=true the sort kernel reads from this same texture,
        // so no separate sort-worker refresh is needed for visual correctness.
        sm.updateDataTexturesFromBaseData(0, n - 1);
      }
    }
    // FPS counter — measure raw R3F frame loop, not the lib's internal counter.
    frames.current++;
    const now = performance.now();
    if (now - lastFpsT.current > 500) {
      onFps((frames.current * 1000) / (now - lastFpsT.current));
      frames.current = 0;
      lastFpsT.current = now;
    }
  });

  // Render the real viewer Group once the effect has built it; before then,
  // a no-op Group placeholder keeps the scene graph valid. The placeholder is
  // memoized so we don't churn objects across renders.
  return viewer ? <primitive object={viewer} /> : null;
}

createRoot(document.getElementById("root")!).render(<App />);
