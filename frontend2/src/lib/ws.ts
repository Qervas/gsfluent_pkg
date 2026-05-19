/**
 * WebSocket client to /v1/stream.
 *
 * Singleton with auto-reconnect (exponential backoff 1s -> 30s).
 * Subscribers register a (channel, handler) pair; the client multiplexes
 * subscribe/unsubscribe to the server based on aggregate refcounts.
 * On reconnect, re-subscribes everything and asks for replay_since.
 */

type EventMsg = Record<string, unknown> & { type: string; seq?: number };
type Handler = (event: EventMsg) => void;

class StreamClient {
  private ws: WebSocket | null = null;
  private subs = new Map<string, Set<Handler>>();
  private lastSeqByChannel = new Map<string, number>();
  private reconnectMs = 1_000;
  private url: string;
  private opening = false;
  private closed = false;

  constructor(url: string) {
    this.url = url;
  }

  open(): void {
    if (this.opening || (this.ws && this.ws.readyState === WebSocket.OPEN)) return;
    this.opening = true;
    const ws = new WebSocket(this.url);
    this.ws = ws;

    ws.addEventListener("open", () => {
      this.opening = false;
      this.reconnectMs = 1_000;
      // Re-subscribe + ask for replay_since for everything we know.
      const channels = [...this.subs.keys()];
      if (channels.length) {
        ws.send(JSON.stringify({ subscribe: channels }));
        if (this.lastSeqByChannel.size) {
          ws.send(
            JSON.stringify({
              replay_since: Object.fromEntries(this.lastSeqByChannel.entries()),
            }),
          );
        }
      }
    });

    ws.addEventListener("message", (ev) => {
      let msg: EventMsg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      // Find the channel by event type/run_id/session_id; track seq.
      for (const channel of this.channelsFor(msg)) {
        if (typeof msg.seq === "number") this.lastSeqByChannel.set(channel, msg.seq);
        const handlers = this.subs.get(channel);
        if (!handlers) continue;
        for (const h of handlers) h(msg);
      }
    });

    ws.addEventListener("close", () => {
      this.opening = false;
      this.ws = null;
      if (this.closed) return;
      const wait = this.reconnectMs;
      this.reconnectMs = Math.min(this.reconnectMs * 2, 30_000);
      setTimeout(() => this.open(), wait);
    });

    ws.addEventListener("error", () => {
      // close fires after error; reconnect handled there.
    });
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
    this.ws = null;
  }

  private channelsFor(msg: EventMsg): string[] {
    // Mirror channel_for() in apps/api events.py.
    const runId = msg["run_id"] as string | undefined;
    const sessionId = msg["session_id"] as string | undefined;
    if (msg.type === "log.line" && runId) return [`events:logs:${runId}`];
    if (runId) return [`events:runs:${runId}`];
    if (sessionId) return [`events:render-session:${sessionId}`];
    return [];
  }

  subscribe(channel: string, handler: Handler): () => void {
    let set = this.subs.get(channel);
    if (!set) {
      set = new Set();
      this.subs.set(channel, set);
      this.ws?.send?.(JSON.stringify({ subscribe: [channel] }));
    }
    set.add(handler);
    this.open();
    return () => {
      const s = this.subs.get(channel);
      if (!s) return;
      s.delete(handler);
      if (s.size === 0) {
        this.subs.delete(channel);
        this.ws?.send?.(JSON.stringify({ unsubscribe: [channel] }));
      }
    };
  }
}

const wsUrl =
  (window.location.protocol === "https:" ? "wss://" : "ws://") +
  window.location.host +
  "/v1/stream";

export const streamClient = new StreamClient(wsUrl);
export type { EventMsg };
