import { useEffect } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { DoubleSide } from "three";
import type * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { useStore } from "@/lib/store";
import { SplatScene } from "./SplatScene";
import { GaussianSplatScene } from "./GaussianSplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";
import { RenderModeToggle } from "./RenderModeToggle";
import { FpsIndicator } from "./FpsIndicator";

/**
 * Single source of truth for camera.up. Both modes are Z-up: our team's
 * 3DGS captures (and most COLMAP-derived scans) bake gravity into the
 * world's +Z axis. Splat mode used to default to Y-up matching a stale
 * "splat-test.html" config, which made Z-up buildings render lying on
 * their side. Unifying to Z-up keeps the building upright and removes
 * the orientation flip on Splat ↔ Points toggle.
 *
 * If a future Y-up dataset shows up (e.g. PhysGaussian ficus) we'll add
 * a per-model up-axis override on the ModelItem rather than re-globalize.
 */
function UpAxisSync() {
  const { camera, controls } = useThree() as unknown as {
    camera: THREE.PerspectiveCamera;
    controls: OrbitControlsImpl | null;
  };
  useEffect(() => {
    camera.up.set(0, 0, 1);
    controls?.update?.();
  }, [camera, controls]);
  return null;
}

export function Viewport() {
  const staticAttrs = useStore((s) => s.staticAttrs);
  const sceneScale = useStore((s) => s.sceneScale);
  const sceneCenter = useStore((s) => s.sceneCenter);
  const sceneFloor = useStore((s) => s.sceneFloor);
  const renderMode = useStore((s) => s.renderMode);
  const simRunName = useStore((s) => s.simRunName);
  // Splat mode is available for both static model preview AND sim run
  // playback. Static models bootstrap from /api/models/file/...; sim runs
  // bootstrap from /api/runs/<name>/frame/0.ply and then accept per-frame
  // xyz updates from the WS stream pushed into the splat mesh's centers
  // texture (see GaussianSplatScene "sim mode").
  const isModelPreview =
    typeof simRunName === "string" && simRunName.startsWith("_model:");
  const isSimRun =
    typeof simRunName === "string" && simRunName.length > 0 && !isModelPreview;
  const splatAvailable = isModelPreview || isSimRun;
  const effectiveMode = splatAvailable && renderMode === "splat" ? "splat" : "points";

  // Scale the grid + fade to the active model. Without this, models living
  // at large world coords push the camera so far from world-origin that the
  // default infinite grid is clipped invisible.
  const cellSize    = Math.max(sceneScale / 50, 0.001);
  const sectionSize = Math.max(sceneScale / 5, 0.01);
  const fadeDistance = Math.max(sceneScale * 4, 50);

  return (
    <div className="h-full w-full relative bg-canvas">
      <Canvas
        camera={{
          position: [5, 5, 6],
          fov: 50,
          up: [0, 0, 1],
        }}
      >
        {/* Grid is always Z-up: rotated +π/2 around X to lie on XY plane,
            with sceneFloor as bbox.min along world Z. */}
        <Grid
          args={[200, 200]}
          cellSize={cellSize}
          sectionSize={sectionSize}
          cellColor="#21262d"
          sectionColor="#22d3ee"
          sectionThickness={0.6}
          fadeDistance={fadeDistance}
          fadeStrength={1}
          followCamera={false}
          infiniteGrid
          rotation={[Math.PI / 2, 0, 0]}
          position={[sceneCenter[0], sceneCenter[1], sceneFloor]}
          side={DoubleSide}
        />
        <OrbitControls
          makeDefault
          enableDamping
          dampingFactor={0.08}
          minPolarAngle={0.01}
          maxPolarAngle={Math.PI - 0.01}
          minDistance={0.001}
          maxDistance={Infinity}
        />
        <GizmoHelper alignment="bottom-left" margin={[60, 60]}>
          <GizmoViewport
            axisColors={["#f87171", "#34d399", "#22d3ee"]}
            labelColor="#0d1117"
          />
        </GizmoHelper>
        <UpAxisSync />
        {/* In-place mode swap: same canvas, same camera, same world.
            Grid + gizmo + controls stay; only the data renderer changes. */}
        {staticAttrs && effectiveMode === "points" && <SplatScene />}
        {effectiveMode === "splat" && <GaussianSplatScene />}
      </Canvas>
      {!staticAttrs && <EmptyState />}
      <DropZone />
      <RenderModeToggle splatAvailable={splatAvailable} />
      <FpsIndicator />
    </div>
  );
}
