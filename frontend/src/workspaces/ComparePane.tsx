import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import * as THREE from "three";
import type { StaticAttrs } from "@/lib/types";
import { StreamClient } from "@/lib/ws";

/**
 * Owns its own WebSocket subscription, local staticAttrs + frames Map, and
 * R3F Canvas — keeps the existing single-slot zustand store untouched so two
 * panes can stream different runs simultaneously.
 */
export function ComparePane({
  runName,
  currentFrame,
  onFrameCount,
}: {
  runName: string;
  currentFrame: number;
  onFrameCount: (n: number) => void;
}) {
  const [staticAttrs, setStaticAttrs] = useState<StaticAttrs | null>(null);
  const [frames, setFrames] = useState<Map<number, Float32Array>>(new Map());

  useEffect(() => {
    // Fresh state per runName change.
    setStaticAttrs(null);
    setFrames(new Map());

    const client = new StreamClient({
      onStaticAttrs: (m) => setStaticAttrs(m.attrs),
      onFrame: (meta, xyz) => {
        setFrames((prev) => {
          const next = new Map(prev);
          next.set(meta.frame_idx, xyz);
          return next;
        });
      },
    });
    client.connect();
    client.subscribe(runName);
    return () => {
      client.unsubscribe();
      // No client.disconnect() — StreamClient doesn't expose it; relying on
      // the WebSocket onclose. For a tighter cleanup, add disconnect() to
      // StreamClient and call it here.
    };
  }, [runName]);

  // Report total frame count up to the parent so the synchronized slider
  // knows the max. Use the highest known index + 1.
  useEffect(() => {
    if (frames.size === 0) return;
    const maxIdx = Math.max(...frames.keys());
    onFrameCount(maxIdx + 1);
  }, [frames, onFrameCount]);

  return (
    <Canvas camera={{ position: [3, 3, 3], fov: 50, up: [0, 0, 1] }}>
      <Grid
        args={[20, 20]}
        cellColor="#21262d"
        sectionColor="#22d3ee"
        sectionThickness={0.6}
        fadeDistance={30}
        infiniteGrid
        rotation={[-Math.PI / 2, 0, 0]}
      />
      <OrbitControls
        makeDefault
        enableDamping
        dampingFactor={0.08}
        minPolarAngle={0.01}
        maxPolarAngle={Math.PI - 0.01}
      />
      <GizmoHelper alignment="bottom-left" margin={[40, 40]}>
        <GizmoViewport
          axisColors={["#f87171", "#34d399", "#22d3ee"]}
          labelColor="#0d1117"
        />
      </GizmoHelper>
      {staticAttrs && (
        <CompareScene
          staticAttrs={staticAttrs}
          frames={frames}
          currentFrame={currentFrame}
        />
      )}
    </Canvas>
  );
}

function CompareScene({
  staticAttrs,
  frames,
  currentFrame,
}: {
  staticAttrs: StaticAttrs;
  frames: Map<number, Float32Array>;
  currentFrame: number;
}) {
  const { camera, controls } = useThree() as unknown as {
    camera: THREE.PerspectiveCamera;
    controls: any;
  };
  const fittedFor = useRef<unknown>(null);
  const positionsRef = useRef<THREE.BufferAttribute | null>(null);
  const [pointSize, setPointSize] = useState(0.05);

  const built = useMemo(() => {
    if (!staticAttrs || staticAttrs.n === 0) return null;
    return {
      positions: new Float32Array(staticAttrs.n * 3),
      colors: new Float32Array(staticAttrs.rgb),
      n: staticAttrs.n,
    };
  }, [staticAttrs]);

  // Auto-fit + initial seed.
  useEffect(() => {
    if (!built) return;
    if (fittedFor.current === staticAttrs) return;
    const f0 = frames.get(0);
    if (!f0 || f0.length !== built.positions.length) return;

    built.positions.set(f0);
    if (positionsRef.current) positionsRef.current.needsUpdate = true;

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
    const camPos = center.clone().add(
      new THREE.Vector3(diag * 1.0, diag * 1.0, diag * 0.7),
    );
    camera.position.copy(camPos);
    camera.near = Math.max(diag * 0.001, 0.01);
    camera.far = diag * 100;
    camera.updateProjectionMatrix();
    camera.lookAt(center);
    if (controls?.target?.copy) {
      controls.target.copy(center);
      controls.update?.();
    }
    setPointSize(Math.max(diag * 0.004, 0.005));
    fittedFor.current = staticAttrs;
  }, [built, frames, staticAttrs, camera, controls]);

  // Per-render: pull the synced currentFrame and write to positions.
  useFrame(() => {
    if (!built) return;
    const xyz = frames.get(currentFrame);
    if (!xyz || xyz.length !== built.positions.length) return;
    built.positions.set(xyz);
    if (positionsRef.current) positionsRef.current.needsUpdate = true;
  });

  if (!built) return null;
  return (
    <points key={built.n}>
      <bufferGeometry>
        <bufferAttribute
          ref={positionsRef}
          attach="attributes-position"
          args={[built.positions, 3]}
        />
        <bufferAttribute attach="attributes-color" args={[built.colors, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={pointSize}
        vertexColors
        sizeAttenuation
        transparent={false}
      />
    </points>
  );
}
