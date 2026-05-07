import { useEffect, useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import * as Splat from "@mkkellogg/gaussian-splats-3d";
import * as THREE from "three";
import { useStore } from "@/lib/store";
import { packToSplatBuffer } from "./splat-helpers";

/**
 * SplatScene — renders the active 3DGS splats inside the R3F scene.
 *
 * Mounts a DropInViewer once `staticAttrs` arrives, builds the initial
 * SplatBuffer from frame 0's xyz + the static per-particle (R, scales,
 * rgb, opacity) attributes, then mutates `splatDataTextures.baseData
 * .centers` in place each render to drive live animation.
 *
 * API conventions match the Phase 0 spike (`spike/splat-test/main.tsx`):
 * - DropInViewer with `dynamicScene: true` (mandatory for live updates)
 * - `gpuAcceleratedSort: true`, `sphericalHarmonicsDegree: 0`
 * - `addSplatBuffers` 8-positional-arg form
 * - `updateDataTexturesFromBaseData(0, n - 1)` to push centers to GPU
 */
export function SplatScene() {
  const [viewer, setViewer] = useState<any>(null);
  const viewerRef = useRef<any>(null);
  const initialFrameSent = useRef(false);

  const staticAttrs = useStore((s) => s.staticAttrs);
  const frameXyz = useStore((s) => s.frameXyz);
  const currentFrameIdx = useStore((s) => s.currentFrameIdx);
  const playing = useStore((s) => s.playing);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);

  // Set up viewer once when staticAttrs first arrives.
  useEffect(() => {
    if (!staticAttrs) return;
    const dropIn = new (Splat as any).DropInViewer({
      gpuAcceleratedSort: true,
      sharedMemoryForWorkers: false,
      dynamicScene: true,
      sphericalHarmonicsDegree: 0,
    });
    viewerRef.current = dropIn;
    setViewer(dropIn);
    initialFrameSent.current = false;

    return () => {
      dropIn.dispose?.().catch(() => {});
      if (viewerRef.current === dropIn) {
        viewerRef.current = null;
      }
      setViewer(null);
      initialFrameSent.current = false;
    };
  }, [staticAttrs]);

  // Push first frame data when it arrives. Live updates happen in useFrame.
  useEffect(() => {
    if (!viewerRef.current || !staticAttrs || initialFrameSent.current) return;
    const f0 = frameXyz.get(0);
    if (!f0) return;
    // Build via the exported SplatParser path. UncompressedSplatArray is
    // defined inside the lib but never re-exported, so we cannot call its
    // constructor from outside; instead we pack to the standard .splat
    // 32-bytes-per-splat binary format and let the parser materialise the
    // array for us.
    const SplatNs = Splat as any;
    const splatBuf = packToSplatBuffer(staticAttrs, f0);
    const arr = SplatNs.SplatParser.parseStandardSplatToUncompressedSplatArray(splatBuf);
    const generator = SplatNs.SplatBufferGenerator.getStandardGenerator(
      0, 0, 0, new THREE.Vector3(),
    );
    const splatBuffer = generator.generateFromUncompressedSplatArray(arr);
    viewerRef.current.viewer.addSplatBuffers(
      [splatBuffer],
      [{}],
      true,   // finalBuild
      false,  // showLoadingUI
      false,  // showLoadingUIForSplatTreeBuild
      false,  // replaceExisting
      true,   // enableRenderBeforeFirstSort
      true,   // preserveVisibleRegion
    );
    initialFrameSent.current = true;
  }, [frameXyz, staticAttrs]);

  // Per render: advance frame + update centers in place.
  // Target ~24 fps for animation step; render loop is decoupled and faster.
  const lastAdvance = useRef<number>(0);
  useFrame(({ clock }) => {
    const v = viewerRef.current;
    if (!v || !staticAttrs) return;

    if (playing && frameXyz.size > 1) {
      const now = clock.elapsedTime;
      if (now - lastAdvance.current > 1 / 24) {
        const next = (currentFrameIdx + 1) % frameXyz.size;
        setCurrentFrame(next);
        lastAdvance.current = now;
      }
    }

    const xyz = frameXyz.get(currentFrameIdx);
    if (!xyz) return;
    const sm = v.splatMesh;
    if (!sm?.splatDataTextures?.baseData?.centers) return;
    const buf: Float32Array = sm.splatDataTextures.baseData.centers;
    if (buf.length === xyz.length) {
      buf.set(xyz);
      sm.updateDataTexturesFromBaseData(0, staticAttrs.n - 1);
    }
  });

  if (!viewer) return null;
  return <primitive object={viewer} />;
}
