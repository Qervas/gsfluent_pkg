import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
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

  // Build buffers when staticAttrs changes. Point size is computed AFTER the
  // first frame arrives (in the auto-fit effect below) — derived from the
  // model's bbox diagonal, NOT from per-splat scales. The scales field is
  // unreliable across the two emitters (Inria 3DGS plys vs our fused
  // frames) and led to invisible-on-static, sometimes-OK-on-sim renders.
  // Bbox-relative sizing is scale-invariant and always produces a visible
  // point cloud.
  const built = useMemo(() => {
    if (!staticAttrs) return null;
    const n = staticAttrs.n;
    if (n === 0) return null;
    return {
      positions: new Float32Array(n * 3),
      colors: new Float32Array(staticAttrs.rgb),
      n,
    };
  }, [staticAttrs]);

  // Live point size — set by the auto-fit effect, mutated by the user later.
  // Default chosen so a brand-new scene doesn't render as a single dot before
  // the bbox calculation completes.
  const [pointSize, setPointSize] = useState<number>(0.05);

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
    controls: OrbitControlsImpl | null;  // wired by drei <OrbitControls makeDefault>
  };

  useEffect(() => {
    if (!built) return;
    // OrbitControls (set via makeDefault) becomes available a tick after
    // mount; if we run auto-fit before then we'd update camera.position
    // but never controls.target, and OrbitControls.update() each frame
    // would yank the camera back toward its default target (world origin).
    // Skip until controls is wired; effect re-runs on the controls dep.
    if (!controls) return;
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
    // and-to-the-side of the centroid. Up-axis itself is owned by
    // <UpAxisSync> in Viewport — it's invariant of staticAttrs timing.
    const camPos = center.clone().add(
      new THREE.Vector3(diag * 1.0, diag * 1.0, diag * 0.7)
    );
    camera.position.copy(camPos);
    camera.near = Math.max(diag * 0.001, 0.01);
    // far must cover the camera-to-origin distance too — drei's Grid is
    // anchored at world origin and gets clipped if far is shorter than the
    // distance from the camera to (0,0,0). For a model at (3460, 29045, 5)
    // the camera ends up ~29000 units from origin; far needs to comfortably
    // exceed that.
    const distToOrigin = camPos.length();
    camera.far = Math.max(diag * 100, distToOrigin * 2);
    camera.updateProjectionMatrix();
    camera.lookAt(center);

    if (typeof controls.target?.copy !== "function") return;
    controls.target.copy(center);
    controls.update?.();

    // Bbox-derived point size: 0.4% of the diagonal. Scale-invariant — works
    // for a 30-unit-diag building or a 30,000-unit-diag city. Tuned so a
    // typical 200k-splat building reads as a continuous surface, not pixels.
    const newPointSize = Math.max(diag * 0.004, 0.005);
    setPointSize(newPointSize);

    // Publish sceneScale + center so the Grid can scale to match.
    useStore.getState().setSceneScale(diag, [center.x, center.y, center.z]);
    // Publish the bbox bottom (world Z) so the Grid sits underneath the
    // model. Using bbox.min.z instead of 0 ensures the grid acts as a
    // floor — visible from above even when the model has negative-Z extent.
    useStore.getState().setSceneFloor(bbox.min.z);

    fittedFor.current = staticAttrs;
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.log("[SplatScene] auto-fit camera:", {
        n: built.n,
        center: center.toArray(),
        size: size.toArray(),
        diag,
        pointSize: newPointSize,
      });
    }
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
    // frustumCulled={false}: the geometry's boundingSphere is computed from
    // the INITIAL position buffer (all zeros — at world origin, radius 0)
    // and is NOT recomputed when we mutate positions in useFrame. So with
    // culling enabled, Three.js drops the whole point cloud whenever world
    // origin is outside the view frustum — which is most of the time for
    // models living at large world coords like (3460, 29045, 5). Disabling
    // is the cheap, correct fix for our case (~200k-700k points always
    // intended to be visible when in viewport).
    <points key={built.n /* force remount when model changes */} frustumCulled={false}>
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
        size={pointSize}
        vertexColors
        sizeAttenuation
        transparent={false}
        opacity={1.0}
      />
    </points>
  );
}
