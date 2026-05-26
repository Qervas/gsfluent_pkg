/** Decode worker: download a .gsq, then answer frame requests off the main
 *  thread. Protocol (postMessage):
 *    main -> worker: { type: "open", url }      | { type: "frame", idx }
 *    worker -> main: { type: "progress", received, total }
 *                  | { type: "ready", static }
 *                  | { type: "frame", idx, positions, quats }  (buffers transferred)
 *                  | { type: "error", message }
 */
import { downloadGsq } from "./download";
import { GsqDecoder, GsqStatic } from "./decoder";

export type WorkerRequest =
  | { type: "open"; url: string }
  | { type: "frame"; idx: number };

export type WorkerResponse =
  | { type: "progress"; received: number; total: number | null }
  | { type: "ready"; static: GsqStatic }
  | { type: "frame"; idx: number; positions: Float32Array; quats: Float32Array }
  | { type: "error"; message: string };

let decoder: GsqDecoder | null = null;

function post(msg: WorkerResponse, transfer?: Transferable[]) {
  (self as unknown as Worker).postMessage(msg, transfer ?? []);
}

self.onmessage = async (e: MessageEvent<WorkerRequest>) => {
  const msg = e.data;
  try {
    if (msg.type === "open") {
      const buf = await downloadGsq(msg.url, (p) =>
        post({ type: "progress", received: p.received, total: p.total }),
      );
      decoder = new GsqDecoder(buf);
      post({ type: "ready", static: decoder.static });
    } else if (msg.type === "frame") {
      if (!decoder) throw new Error("frame requested before open");
      const f = decoder.decodeFrame(msg.idx);
      post({ type: "frame", idx: msg.idx, positions: f.positions, quats: f.quats },
        [f.positions.buffer, f.quats.buffer]);
    }
  } catch (err) {
    post({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
};
