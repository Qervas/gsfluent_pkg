import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import { useStore } from "@/lib/store";

/**
 * Renders the active 3DGS data as a Three.js Points cloud and auto-fits the
 * camera to the model's bounding box on first frame.
 *
 * Bug-fix patterns vs the previous version:
 *   - The geometry is keyed on `staticAttrs.n` so React fully unmounts +
 *     remounts the `<points>` when the model changes. Prevents Three.js
 *     from reusing a stale BufferAttribute with the wrong array length.
 *   - The bufferAttribute uses `args={[arr, itemSize]}` ONLY (no `array=`
 *     duplicate) — the args form is the canonical R3F constructor pass.
 *   - On first frame for a given staticAttrs, we compute the bbox and snap
 *     OrbitControls' target + camera position to a sensible framing. The
 *     model can be at world (3460, 29045, 5) and the camera will follow.
 */
export function SplatScene() {
  const staticAttrs = useStore((s) => s.staticAttrs);
  const frameXyz = useStore((s) => s.frameXyz);
  const currentFrameIdx = useStore((s) => s.currentFrameIdx);
  const playing = useStore((s) => s.playing);
  const setCurrentFrame = useStore((s) => s.setCurrentFrame);

  // We snap the camera ONCE per (staticAttrs identity) — track via ref.
  const fittedFor = useRef<unknown>(null);
  const positionsRef = useRef<THREE.BufferAttribute | null>(null);
  const lastAdvance = useRef<number>(0);

  // Build buffers + heuristic point size when staticAttrs changes.
  const built = useMemo(() => {
    if (!staticAttrs) return null;
    const n = staticAttrs.n;
    if (n === 0) return null;

    const positions = new Float32Array(n * 3);
    const colors = new Float32Array(staticAttrs.rgb);

    // Heuristic point size: median of `max(sx,sy,sz)` × 6 across a sample of
    // the splats, giving a size that scales with model size. Fall back to a
    // small default for synthetic / xyz-only plys whose scales are uniform.
    let pointSize = 0.01;
    if (staticAttrs.scales && staticAttrs.scales.length >= 3 * n) {
      const sample: number[] = [];
      const stride = Math.max(1, Math.floor(n / 256));
      for (let i = 0; i < n; i += stride) {
        const sx = staticAttrs.scales[i * 3 + 0];
        const sy = staticAttrs.scales[i * 3 + 1];
        const sz = staticAttrs.scales[i * 3 + 2];
        sample.push(Math.max(sx, sy, sz));
      }
      sample.sort((a, b) => a - b);
      const med = sample[Math.floor(sample.length / 2)] || 0.01;
      pointSize = Math.max(0.001, med * 6);
    }

    return { positions, colors, pointSize, n };
  }, [staticAttrs]);

  // Seed the position buffer from frame 0 the moment it arrives. Without this,
  // the geometry would render at all-zeros until the playback loop advances.
  useEffect(() => {
    if (!built) return;
    const f0 = frameXyz.get(0);
    if (f0 && f0.length === built.positions.length) {
      built.positions.set(f0);
      if (positionsRef.current) positionsRef.current.needsUpdate = true;
    }
  }, [built, frameXyz]);

  // Auto-fit the camera to the model's bbox on first frame.
  const { camera, controls } = useThree() as unknown as {
    camera: THREE.PerspectiveCamera;
    controls: any;  // OrbitControls instance from drei (makeDefault)
  };

  useEffect(() => {
    if (!built) return;
    if (fittedFor.current === staticAttrs) return;
    const f0 = frameXyz.get(0);
    if (!f0 || f0.length !== built.positions.length) return;

    const bbox = new THREE.Box3();
    const v = new THREE.Vector3();
    for (let i = 0; i < built.n; i++) {
      v.set(f0[i * 3], f0[i * 3 + 1], f0[i * 3 + 2]);
      bbox.expandByPoint(v);
    }
    if (bbox.isEmpty()) return;

    const center = new THREE.Vector3();
    bbox.getCenter(center);
    const size = new THREE.Vector3();
    bbox.getSize(size);
    const diag = size.length() || 1;

    // Place the camera 1.5× the diagonal away from the center, looking down
    // and at an angle so we see all three axes. Z-up world: position above-
    // and-to-the-side of the centroid.
    const camPos = center.clone().add(
      new THREE.Vector3(diag * 1.0, diag * 1.0, diag * 0.7)
    );
    camera.position.copy(camPos);
    camera.near = Math.max(diag * 0.001, 0.01);
    camera.far = diag * 100;
    camera.updateProjectionMatrix();
    camera.lookAt(center);

    if (controls && typeof controls.target?.copy === "function") {
      controls.target.copy(center);
      controls.update?.();
    }

    fittedFor.current = staticAttrs;
    // eslint-disable-next-line no-console
    console.log("[SplatScene] auto-fit camera:", {
      n: built.n,
      center: center.toArray(),
      size: size.toArray(),
      diag,
      pointSize: built.pointSize,
    });
  }, [built, frameXyz, staticAttrs, camera, controls]);

  // Per render: advance frame + update buffer in place.
  useFrame(({ clock }) => {
    if (!built) return;
    if (playing && frameXyz.size > 1) {
      const now = clock.elapsedTime;
      if (now - lastAdvance.current > 1 / 24) {
        const next = (currentFrameIdx + 1) % frameXyz.size;
        setCurrentFrame(next);
        lastAdvance.current = now;
      }
    }
    const xyz = frameXyz.get(currentFrameIdx);
    if (!xyz || xyz.length !== built.positions.length) return;
    built.positions.set(xyz);
    if (positionsRef.current) positionsRef.current.needsUpdate = true;
  });

  if (!built) return null;

  return (
    <points key={built.n /* force remount when model changes */}>
      <bufferGeometry>
        <bufferAttribute
          ref={positionsRef}
          attach="attributes-position"
          args={[built.positions, 3]}
        />
        <bufferAttribute
          attach="attributes-color"
          args={[built.colors, 3]}
        />
      </bufferGeometry>
      <pointsMaterial
        size={built.pointSize}
        vertexColors
        sizeAttenuation
        transparent={false}
        opacity={1.0}
      />
    </points>
  );
}
