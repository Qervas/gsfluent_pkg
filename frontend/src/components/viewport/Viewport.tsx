import { useEffect, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { DoubleSide } from "three";
import type * as THREE from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { useStore } from "@/lib/store";
import { useActiveCell } from "@/lib/use-active-cell";
import { SplatScene } from "./SplatScene";
import { ViserSplatScene } from "./ViserSplatScene";
import { EmptyState } from "./EmptyState";
import { DropZone } from "./DropZone";
import { RenderModeToggle } from "./RenderModeToggle";
import { FpsIndicator } from "./FpsIndicator";
import { PlaybackDriver } from "./PlaybackDriver";
import { PlaybackBar } from "./PlaybackBar";

/**
 * Single source of truth for camera.up. Both modes are Z-up: our team's
 * 3DGS captures (and most COLMAP-derived scans) bake gravity into the
 * world's +Z axis. Splat mode used to default to Y-up from an early
 * prototype config, which made Z-up buildings render lying on their
 * side. Unifying to Z-up keeps the building upright and removes the
 * orientation flip on Splat ↔ Points toggle.
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
  const { activeCell } = useActiveCell();
  // Splat mode is available for both static model preview AND sim run
  // playback. Static models bootstrap from /api/models/file/...; sim runs
  // come from /api/runs/<name>/frame/0.ply. Viser handles both kinds
  // behind the same control API now, so any non-null cell is splat-eligible.
  const splatAvailable = !!activeCell;
  const effectiveMode = splatAvailable && renderMode === "splat" ? "splat" : "points";

  // Scale the grid + fade to the active model. Without this, models living
  // at large world coords push the camera so far from world-origin that the
  // default infinite grid is clipped invisible.
  const cellSize    = Math.max(sceneScale / 50, 0.001);
  const sectionSize = Math.max(sceneScale / 5, 0.01);
  const fadeDistance = Math.max(sceneScale * 4, 50);

  // Camera sync across mode toggle. The Points-mode SplatScene continuously
  // writes the user's orbit state to `pointsCamera`; on transition we
  // either push that state into viser (Points→Splat) or pull viser's
  // current view back out (Splat→Points) so the user's chosen viewpoint
  // carries across mode toggles instead of snapping back to defaults.
  const prevModeRef = useRef(renderMode);
  useEffect(() => {
    const prev = prevModeRef.current;
    prevModeRef.current = renderMode;
    if (prev === renderMode) return;
    // Mixed-content guard: if the SPA is served over https, browsers
    // silently block http://localhost:8092 fetches and the camera
    // sync looks "broken." Surface the misconfiguration once instead
    // of failing in stealth.
    const envControl = import.meta.env.VITE_VISER_CONTROL_URL as string | undefined;
    if (location.protocol === "https:" && (!envControl || envControl.startsWith("http:"))) {
      // eslint-disable-next-line no-console
      console.warn(
        "[Viewport] SPA loaded over https but VITE_VISER_CONTROL_URL is " +
        "http (or unset). Browser will block mixed content — camera sync " +
        "will no-op. Set VITE_VISER_CONTROL_URL=https://… in your .env.",
      );
      return;
    }
    const controlUrl = envControl || `http://${location.hostname}:8092`;
    if (renderMode === "splat") {
      // points → splat: push our cached pointsCamera into viser. If we
      // have no cached state yet (user never orbited), skip — viser's own
      // bbox-fitted initial camera will frame the scene.
      const cam = useStore.getState().pointsCamera;
      if (!cam) return;
      fetch(`${controlUrl}/camera`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ position: cam.position, target: cam.target }),
      }).catch(() => {
        /* viser unreachable; nothing to surface here */
      });
    } else {
      // splat → points: pull viser's last view back into the store, so
      // SplatScene's auto-fit honors it on remount.
      fetch(`${controlUrl}/camera`)
        .then((r) => r.json())
        .then((d) => {
          if (d && Array.isArray(d.position) && Array.isArray(d.target)) {
            useStore.getState().setPointsCamera({
              position: d.position as [number, number, number],
              target:   d.target   as [number, number, number],
            });
          }
        })
        .catch(() => {});
    }
  }, [renderMode]);

  return (
    <div className="h-full w-full relative bg-canvas">
      {effectiveMode === "splat" ? (
        // Splats mode: viser runs headless behind the iframe and is driven
        // by ViserSplatScene's control-API POSTs. Sequence picker and
        // PlaybackBar feed (cell, frame) into the same `viser_headless.py`
        // process; viser owns rendering, React owns everything else.
        <ViserSplatScene />
      ) : (
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
          {staticAttrs && <SplatScene />}
        </Canvas>
      )}
      {/* Frame advance lives outside the R3F Canvas so both Points (R3F)
          and Splats (viser iframe) modes share the same ticker —
          PlaybackDriver bumps `currentFrameIdx` in the Zustand store, and
          ViserSplatScene's effect forwards each bump to the control API. */}
      <PlaybackDriver />
      {!staticAttrs && <EmptyState />}
      <DropZone />
      <RenderModeToggle splatAvailable={splatAvailable} />
      <FpsIndicator />
      <PlaybackBar />
    </div>
  );
}
