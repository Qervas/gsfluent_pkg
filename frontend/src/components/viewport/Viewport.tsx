import { Canvas } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { useStore } from "@/lib/store";
import { SplatScene } from "./SplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";

export function Viewport() {
  const staticAttrs = useStore((s) => s.staticAttrs);
  const sceneScale = useStore((s) => s.sceneScale);
  const sceneCenter = useStore((s) => s.sceneCenter);

  // Scale the grid + fade to the active model. Without this, models living
  // at large world coords (e.g. cluster_6_15 at ~3460, 29045) push the
  // camera so far from world-origin that the default infinite grid (anchored
  // near origin with fadeDistance=30) is clipped invisible.
  const cellSize    = Math.max(sceneScale / 50, 0.001);
  const sectionSize = Math.max(sceneScale / 5, 0.01);
  const fadeDistance = Math.max(sceneScale * 4, 50);

  return (
    <div className="h-full w-full relative bg-canvas">
      <Canvas
        camera={{
          // Tilted enough off-axis that the gimbal singularity isn't on the
          // initial frame; user can orbit smoothly from any starting angle.
          position: [5, 5, 6],
          fov: 50,
          up: [0, 0, 1],
        }}
        // Far defaults are scene-dependent; SplatScene auto-fit will set far
        // when a model arrives. For the empty-state default, give plenty of
        // room so the infinite grid renders at all angles.
      >
        {/* Grid lies on XY plane (Z-up convention). Position is set to the
            scene center so the grid sits directly under the active model
            regardless of world-coord magnitude. side=DoubleSide is implicit
            in drei's Grid; visible from above and below. */}
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
          rotation={[-Math.PI / 2, 0, 0]}
          position={[sceneCenter[0], sceneCenter[1], 0]}
        />
        {/* OrbitControls with explicit polar limits avoiding the singular
            poles by ~0.5° on each side. With up=[0,0,1] and Three.js's
            spherical-coordinate orbit math, polarAngle ∈ {0, π} is a gimbal
            singularity — the camera "locks" or flips erratically right at
            those points. Clamping just inside lets users look very nearly
            straight-down (top-down view) and very nearly straight-up
            without hitting the broken pole. enableDamping smooths inertia. */}
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
        {staticAttrs && <SplatScene />}
      </Canvas>
      {!staticAttrs && <EmptyState />}
      <DropZone />
    </div>
  );
}
