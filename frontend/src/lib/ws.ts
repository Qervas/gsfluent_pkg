import type { StaticAttrs, FrameMeta } from "./types";

type Handlers = {
  onStatus?: (msg: {
    run_name: string;
    state: string;
    n_frames?: number;
    total_frames?: number;
    fps_observed?: number;
  }) => void;
  onLog?: (msg: { run_name: string; line: string }) => void;
  onStaticAttrs?: (msg: { run_name: string; attrs: StaticAttrs }) => void;
  onFrame?: (meta: FrameMeta, xyz: Float32Array) => void;
  onError?: (msg: { code: string; run_name: string; message: string }) => void;
};

export class StreamClient {
  private ws: WebSocket | null = null;
  private pendingMeta: FrameMeta | null = null;
  private currentRun: string | null = null;
  private pendingLoadModel: string | null = null;
  private reconnectTimer: number | null = null;
  // Was: rotate incoming xyz from Y-up (Inria) to Z-up. Verified
  // empirically (bbox inspection of cluster_6_15) that our captures are
  // already Z-up COLMAP, so this rotation was wrong for our data — it
  // tipped model-preview buildings onto their side in points mode the
  // same way it did in splat mode. Left as a stub so a future per-model
  // up-axis override can re-enable it for genuine Y-up datasets.
  private applyYUpRotation = false;

  constructor(private h: Handlers) {}

  connect(): void {
    // Points-mode WS source. Under the split-topology deployment the
    // SPA loads from the server but the Points stream is served by a
    // *local* mmap'd service on the laptop (tools/local_stream.py) so
    // we don't pay WAN bandwidth for ~120 MB/s of per-frame xyz. The
    // VITE_LOCAL_STREAM_URL env var configures that endpoint; fallback
    // is the same-origin shape so a server-bundled deployment (no
    // local laptop service) still works for testing.
    const fromEnv = import.meta.env.VITE_LOCAL_STREAM_URL as string | undefined;
    let url: string;
    if (fromEnv) {
      url = fromEnv;
    } else {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      url = `${proto}://${location.host}/api/stream`;
    }
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev) => this._onMessage(ev);
    this.ws.onclose = () => this._scheduleReconnect();
    this.ws.onopen = () => {
      // Re-subscribe to a run after reconnect, or fire a deferred
      // load_model that arrived before the socket was open.
      if (this.currentRun) {
        this._send({ type: "subscribe", run_name: this.currentRun });
      } else if (this.pendingLoadModel) {
        this._send({ type: "load_model", path: this.pendingLoadModel });
        this.pendingLoadModel = null;
      }
    };
  }

  subscribe(run_name: string): void {
    this.currentRun = run_name;
    this.pendingLoadModel = null;
    this.applyYUpRotation = false;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ type: "subscribe", run_name });
    }
  }

  unsubscribe(): void {
    this.currentRun = null;
    this.applyYUpRotation = false;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ type: "unsubscribe" });
    }
  }

  /** Render a model's static ply as a single-frame snapshot. Replaces
   *  any active run subscription. If the socket isn't open yet, queue
   *  the request for the next onopen. */
  loadModel(path: string): void {
    this.currentRun = null;
    this.applyYUpRotation = false;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ type: "load_model", path });
    } else {
      this.pendingLoadModel = path;
    }
  }

  private _send(m: object) {
    this.ws?.send(JSON.stringify(m));
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer != null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 1500);
  }

  private _onMessage(ev: MessageEvent) {
    if (typeof ev.data === "string") {
      let msg: any;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "frame_meta") {
        this.pendingMeta = msg;
      } else if (msg.type === "static_attrs") {
        // Disabled: our captures are Z-up COLMAP (verified via bbox).
        // Re-enable per model when we onboard a Y-up dataset.
        this.applyYUpRotation = false;
        this.h.onStaticAttrs?.({ run_name: msg.run_name, attrs: decodeStatic(msg) });
      } else if (msg.type === "status") {
        this.h.onStatus?.(msg);
      } else if (msg.type === "log") {
        this.h.onLog?.(msg);
      } else if (msg.type === "error") {
        this.h.onError?.(msg);
      }
    } else if (ev.data instanceof ArrayBuffer && this.pendingMeta) {
      const xyz = new Float32Array(ev.data);
      if (this.applyYUpRotation) {
        // Inria 3DGS pipelines preserve COLMAP's coordinate convention:
        // +Y points DOWN (into the ground), not up. So the building's
        // sky direction is -Y, and we want -Y → +Z. Rx(-π/2) does that:
        // (x, y, z) → (x, z, -y).
        for (let i = 0; i < xyz.length; i += 3) {
          const y = xyz[i + 1];
          const z = xyz[i + 2];
          xyz[i + 1] = z;
          xyz[i + 2] = -y;
        }
      }
      this.h.onFrame?.(this.pendingMeta, xyz);
      this.pendingMeta = null;
    }
  }
}

function decodeStatic(msg: {
  n: number;
  R_b64: string;
  scales_b64: string;
  rgb_b64: string;
  opacity_b64: string;
}): StaticAttrs {
  // The server emits empty strings for fields the Points renderer doesn't
  // consume (R, scales, opacity) so the static_attrs message doesn't blow
  // past WS message size limits. Skip the atob → Float32Array dance for
  // empty inputs; just return an empty typed array.
  const dec = (b64: string): Float32Array => {
    if (!b64) return new Float32Array(0);
    const bin = atob(b64);
    const a = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
    return new Float32Array(a.buffer);
  };
  return {
    n: msg.n,
    R:       dec(msg.R_b64),
    scales:  dec(msg.scales_b64),
    rgb:     dec(msg.rgb_b64),
    opacity: dec(msg.opacity_b64),
  };
}
