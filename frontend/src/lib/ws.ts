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
  private reconnectTimer: number | null = null;

  constructor(private h: Handlers) {}

  connect(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/api/stream`;
    this.ws = new WebSocket(url);
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev) => this._onMessage(ev);
    this.ws.onclose = () => this._scheduleReconnect();
    this.ws.onopen = () => {
      // Re-subscribe after a reconnect.
      if (this.currentRun) {
        this._send({ type: "subscribe", run_name: this.currentRun });
      }
    };
  }

  subscribe(run_name: string): void {
    this.currentRun = run_name;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ type: "subscribe", run_name });
    }
  }

  unsubscribe(): void {
    this.currentRun = null;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._send({ type: "unsubscribe" });
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
  const dec = (b64: string): Float32Array => {
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
