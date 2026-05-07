import { Canvas } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { useStore } from "@/lib/store";
import { SplatScene } from "./SplatScene";
import { EmptyState } from "./EmptyState";

export function Viewport() {
  const staticAttrs = useStore((s) => s.staticAttrs);

  return (
    <div className="h-full w-full relative bg-canvas">
      <Canvas camera={{ position: [3, 3, 3], fov: 50 }}>
        {/* Floor: 20x20 grid on XY plane (Z-up convention, matches our backend rotation). */}
        <Grid
          args={[20, 20]}
          cellColor="#21262d"
          sectionColor="#22d3ee"
          sectionThickness={0.6}
          fadeDistance={30}
          infiniteGrid
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
    </div>
  );
}
