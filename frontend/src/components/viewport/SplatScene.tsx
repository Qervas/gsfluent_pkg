import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { useStore } from "@/lib/store";

/**
 * SplatScene renders the active 3DGS data as a Three.js Points cloud with
 * per-point vertex colors. This is a pragmatic stand-in for proper Gaussian-
 * splat rendering — visual quality is lower (no anisotropic ellipsoids, no
 * per-point opacity) but the rendering pipeline is dead simple and *actually
 * works*. Phase 1.5+ can swap in @mkkellogg/gaussian-splats-3d once its
 * undocumented API stops being a moving target.
 *
 * Animation: useFrame advances currentFrameIdx at ~24 fps and writes the
 * frame's xyz Float32Array directly into the position attribute's underlying
 * Float32Array, then marks needsUpdate. Allocation-free hot path.
 */
export function SplatScene() {
  const staticAttrs = useStore((s) => s.staticAttrs);
  const frameXyz = useStore((s) => s.frameXyz);
  const currentFrameIdx = useStore((s) => s.currentFrameIdx);
  const playing = useStore((s) => s.playing);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);

  const positionsRef = useRef<THREE.BufferAttribute | null>(null);
  const lastAdvance = useRef<number>(0);

  // Initialize geometry attributes when staticAttrs first arrives. Build the
  // colors Float32Array once (RGB is constant across frames). Position buffer
  // starts empty (zeroed) and gets filled in on first frame arrival via the
  // useFrame copy below.
  const { positions, colors, pointSize } = useMemo(() => {
    if (!staticAttrs) {
      return {
        positions: new Float32Array(0),
        colors: new Float32Array(0),
        pointSize: 0.01,
      };
    }
    const n = staticAttrs.n;
    // Median scale × 4 gives a reasonable point size that covers most of the
    // splat without being so big the cloud looks like a blob.
    let med = 0.005;
    if (staticAttrs.scales.length >= 3 * n) {
      const sample: number[] = [];
      const stride = Math.max(1, Math.floor(n / 256));
      for (let i = 0; i < n; i += stride) {
        sample.push(staticAttrs.scales[i * 3]);
      }
      sample.sort((a, b) => a - b);
      med = sample[Math.floor(sample.length / 2)] || 0.005;
    }
    return {
      positions: new Float32Array(n * 3),
      colors: new Float32Array(staticAttrs.rgb),
      pointSize: Math.max(0.005, Math.min(0.05, med * 4)),
    };
  }, [staticAttrs]);

  // Seed positions with the first frame as soon as it arrives; subsequent
  // frames flow through useFrame's per-render copy.
  useEffect(() => {
    if (!staticAttrs || positions.length === 0) return;
    const f0 = frameXyz.get(0);
    if (f0 && f0.length === positions.length) {
      positions.set(f0);
      if (positionsRef.current) positionsRef.current.needsUpdate = true;
    }
  }, [staticAttrs, frameXyz, positions]);

  useFrame(({ clock }) => {
    if (!staticAttrs || positions.length === 0) return;

    // Advance the playhead at ~24 fps when playing AND we have multiple frames.
    if (playing && frameXyz.size > 1) {
      const now = clock.elapsedTime;
      if (now - lastAdvance.current > 1 / 24) {
        const next = (currentFrameIdx + 1) % frameXyz.size;
        setCurrentFrame(next);
        lastAdvance.current = now;
      }
    }

    const xyz = frameXyz.get(currentFrameIdx);
    if (!xyz || xyz.length !== positions.length) return;
    positions.set(xyz);
    if (positionsRef.current) positionsRef.current.needsUpdate = true;
  });

  if (!staticAttrs || positions.length === 0) return null;
  const n = staticAttrs.n;

  return (
    <points>
      <bufferGeometry>
        <bufferAttribute
          ref={positionsRef}
          attach="attributes-position"
          args={[positions, 3]}
          count={n}
          array={positions}
          itemSize={3}
          needsUpdate
        />
        <bufferAttribute
          attach="attributes-color"
          args={[colors, 3]}
          count={n}
          array={colors}
          itemSize={3}
        />
      </bufferGeometry>
      <pointsMaterial
        size={pointSize}
        vertexColors
        sizeAttenuation
        transparent={false}
        opacity={1.0}
      />
    </points>
  );
}
