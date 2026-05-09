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
 * Single source of truth for camera.up. Each scene component used to set
 * camera.up itself, but SplatScene's assignment was gated on the auto-fit
 * effect which only fires when staticAttrs is loaded; if the user toggled
 * Splat → Points before the WS pump delivered staticAttrs, the camera
 * stayed Y-up while the grid rotated to Z-up. Mounting one effect here,
 * driven by effectiveMode, makes the up-axis invariant of any scene's
 * internal state.
 */
function UpAxisSync({ mode }: { mode: "splat" | "points" }) {
  const { camera, controls } = useThree() as unknown as {
    camera: THREE.PerspectiveCamera;
    controls: OrbitControlsImpl | null;
  };
  useEffect(() => {
    if (mode === "splat") camera.up.set(0, 1, 0);
    else camera.up.set(0, 0, 1);
    controls?.update?.();
  }, [mode, camera, controls]);
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
        {/* Grid orientation tracks the active up-axis convention.
            Points mode = Z-up (rotated +π/2 around X to lie on XY).
            Splat mode = Y-up (drei's default; lies on XZ, no rotation).
            sceneFloor is bbox.min along the up axis in the active mode. */}
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
          rotation={
            effectiveMode === "splat"
              ? [0, 0, 0]
              : [Math.PI / 2, 0, 0]
          }
          position={
            effectiveMode === "splat"
              ? [sceneCenter[0], sceneFloor, sceneCenter[2]]
              : [sceneCenter[0], sceneCenter[1], sceneFloor]
          }
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
        <UpAxisSync mode={effectiveMode} />
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
