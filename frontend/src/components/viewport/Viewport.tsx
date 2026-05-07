import { Canvas } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { useStore } from "@/lib/store";
import { SplatScene } from "./SplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";

export function Viewport() {
  const staticAttrs = useStore((s) => s.staticAttrs);

  return (
    <div className="h-full w-full relative bg-canvas">
      <Canvas camera={{ position: [3, 3, 3], fov: 50, up: [0, 0, 1] }}>
        {/* Grid lies on XY plane (Z-up convention, matching the backend's
            y-up→z-up rotation in frame_stream._M_YUP_TO_ZUP). */}
        <Grid
          args={[20, 20]}
          cellColor="#21262d"
          sectionColor="#22d3ee"
          sectionThickness={0.6}
          fadeDistance={30}
          infiniteGrid
          rotation={[-Math.PI / 2, 0, 0]}
        />
        <OrbitControls makeDefault />
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
