import { useEffect, useRef, useState, useMemo } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, Grid, GizmoHelper, GizmoViewport } from "@react-three/drei";
import * as THREE from "three";
import { Circle, Square, Download } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { StaticAttrs } from "@/lib/types";
import { StreamClient } from "@/lib/ws";
import { Button } from "@/components/ui/button";

export function RenderWorkspace() {
  const { data: history = [] } = useQuery({
    queryKey: ["history"],
    queryFn: api.runs.history,
  });
  const [runName, setRunName] = useState<string | null>(null);
  const [staticAttrs, setStaticAttrs] = useState<StaticAttrs | null>(null);
  const [frames, setFrames] = useState<Map<number, Float32Array>>(new Map());
  const [currentFrame, setCurrentFrame] = useState(0);
  const [playing, setPlaying] = useState(true);

  // Recording state
  const [recording, setRecording] = useState(false);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const [recordedSize, setRecordedSize] = useState<number>(0);
  const [bitrateMbps, setBitrateMbps] = useState(8);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Subscribe to the picked run.
  useEffect(() => {
    if (!runName) {
      setStaticAttrs(null);
      setFrames(new Map());
      return;
    }
    setStaticAttrs(null);
    setFrames(new Map());
    setCurrentFrame(0);
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
    return () => client.unsubscribe();
  }, [runName]);

  const totalFrames = frames.size > 0
    ? Math.max(...frames.keys()) + 1
    : 0;

  // Auto-advance when playing.
  useEffect(() => {
    if (!playing || totalFrames <= 1) return;
    const id = setInterval(() => {
      setCurrentFrame((c) => (c + 1) % totalFrames);
    }, 1000 / 24);
    return () => clearInterval(id);
  }, [playing, totalFrames]);

  // Recording controls.
  const onStart = () => {
    if (!canvasRef.current || !staticAttrs) {
      console.warn("nothing to record yet");
      return;
    }
    setRecordedUrl(null);
    setRecordedSize(0);
    chunksRef.current = [];

    const stream = canvasRef.current.captureStream(30 /* fps hint */);
    // Pick the best supported codec.
    const candidates = [
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm",
      "video/mp4",
    ];
    const mimeType = candidates.find((m) => MediaRecorder.isTypeSupported(m)) ?? "";
    const recorder = new MediaRecorder(stream, {
      mimeType: mimeType || undefined,
      videoBitsPerSecond: bitrateMbps * 1_000_000,
    });
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mimeType || "video/webm" });
      setRecordedUrl(URL.createObjectURL(blob));
      setRecordedSize(blob.size);
    };
    recorder.start(250);
    recorderRef.current = recorder;
    setRecording(true);

    // Force playback so frames advance during recording.
    setPlaying(true);
    setCurrentFrame(0);
  };

  const onStop = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-3 py-2 border-b border-border text-xs">
        <span className="text-text-muted">Source:</span>
        <select
          value={runName ?? ""}
          onChange={(e) => setRunName(e.target.value || null)}
          disabled={recording}
          className="bg-canvas border border-border rounded px-2 py-1 text-text-primary"
        >
          <option value="">— pick a run —</option>
          {history.map((h) => (
            <option key={h.run_name} value={h.run_name}>
              {h.run_name}
            </option>
          ))}
        </select>
        <span className="text-text-muted ml-auto">
          {staticAttrs ? `${staticAttrs.n.toLocaleString()} splats` : "—"} · {totalFrames} frames
        </span>
      </div>

      {/* Canvas */}
      <div className="flex-1 min-h-0 relative">
        <Canvas
          camera={{ position: [3, 3, 3], fov: 50, up: [0, 0, 1] }}
          gl={{ preserveDrawingBuffer: true }}
          onCreated={({ gl }) => {
            // Capture the canvas element for MediaRecorder.captureStream().
            canvasRef.current = gl.domElement;
          }}
        >
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
          <GizmoHelper alignment="bottom-left" margin={[60, 60]}>
            <GizmoViewport
              axisColors={["#f87171", "#34d399", "#22d3ee"]}
              labelColor="#0d1117"
            />
          </GizmoHelper>
          {staticAttrs && (
            <RenderScene
              staticAttrs={staticAttrs}
              frames={frames}
              currentFrame={currentFrame}
            />
          )}
        </Canvas>

        {/* Bottom-right recorder dock */}
        <div className="absolute bottom-3 right-3 bg-elevated/90 backdrop-blur border border-border rounded p-3 space-y-2 text-xs w-72">
          <div className="text-text-secondary uppercase text-[10px] tracking-wider">
            Recorder
          </div>
          <div className="flex items-center gap-2">
            <span className="text-text-muted w-20">Bitrate</span>
            <input
              type="number"
              value={bitrateMbps}
              onChange={(e) => setBitrateMbps(Math.max(1, parseInt(e.target.value) || 1))}
              disabled={recording}
              className="font-mono bg-canvas border border-border rounded px-1 w-16 text-right text-xs"
            />
            <span className="text-text-muted">Mbps</span>
          </div>
          <div className="flex gap-2">
            {!recording ? (
              <Button onClick={onStart} disabled={!staticAttrs}>
                <Circle size={11} fill="currentColor" /> Record
              </Button>
            ) : (
              <Button variant="destructive" onClick={onStop}>
                <Square size={11} fill="currentColor" /> Stop
              </Button>
            )}
            {recordedUrl && (
              <a
                href={recordedUrl}
                download={`${runName ?? "render"}.webm`}
                className="inline-flex items-center gap-1 bg-accent text-canvas px-3 h-7 rounded text-xs font-medium shadow-accent-glow"
              >
                <Download size={11} /> {(recordedSize / 1_000_000).toFixed(1)} MB
              </a>
            )}
          </div>
          <div className="text-[10px] text-text-muted">
            {recording ? "● recording (canvas → webm)" : "Records the live playback canvas. Output is .webm; convert externally for MP4."}
          </div>
        </div>
      </div>

      {/* Timeline */}
      <div className="border-t border-border px-3 py-2 flex items-center gap-2 text-xs font-mono">
        <input
          type="range"
          min={0}
          max={Math.max(0, totalFrames - 1)}
          value={currentFrame}
          onChange={(e) => {
            setCurrentFrame(parseInt(e.target.value));
            setPlaying(false);
          }}
          disabled={recording}
          className="flex-1 accent-accent"
        />
        <span className="text-text-muted">
          {currentFrame}/{Math.max(0, totalFrames - 1)}
        </span>
      </div>
    </div>
  );
}

function RenderScene({
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
    camera.position.copy(center.clone().add(new THREE.Vector3(diag, diag, diag * 0.7)));
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
        <bufferAttribute ref={positionsRef} attach="attributes-position" args={[built.positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[built.colors, 3]} />
      </bufferGeometry>
      <pointsMaterial size={pointSize} vertexColors sizeAttenuation transparent={false} />
    </points>
  );
}
