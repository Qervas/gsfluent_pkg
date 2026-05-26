/** GsqDecoder — headless port of frontend/python/splat_ring.py.
 *  Holds the full .gsq bytes, decodes the static block once, and reconstructs
 *  any frame's absolute int16 (keyframe / sequential-cache / keyframe-walk),
 *  then dequantizes to float. Pure: no DOM / three.js / worker deps.
 */
import { decompress } from "fzstd";
import { parseHeader, GsqHeader } from "./format";
import { dequantize, readF16 } from "./dequant";

export interface GsqStatic {
  nSplats: number;
  nFrames: number;
  fpsHint: number;
  bboxMin: Float32Array;
  bboxMax: Float32Array;
  rgb: Float32Array; // n*3, 0..1
  opacity: Float32Array; // n, 0..1
  scales: Float32Array; // n*3, stddev
}

export interface GsqFrame {
  positions: Float32Array; // n*3
  quats: Float32Array; // n*4, [w, x, y, z]
}

/** a[i] + b[i] stored through an Int16Array so out-of-range sums wrap via
 *  ECMAScript ToInt16 — matches numpy's `.astype(np.int16)`. */
function addI16(a: Int16Array, b: Int16Array): Int16Array {
  const out = new Int16Array(a.length);
  for (let i = 0; i < a.length; i++) out[i] = a[i] + b[i];
  return out;
}

export class GsqDecoder {
  readonly header: GsqHeader;
  readonly static: GsqStatic;
  private readonly buf: Uint8Array;
  private readonly span: Float32Array;
  private lastAbs: { idx: number; xyz: Int16Array; quat: Int16Array } | null = null;

  constructor(bytes: ArrayBuffer | Uint8Array) {
    this.buf = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    this.header = parseHeader(this.buf);
    this.span = new Float32Array(3);
    for (let i = 0; i < 3; i++) {
      const s = this.header.bboxMax[i] - this.header.bboxMin[i];
      this.span[i] = s !== 0 ? s : 1;
    }
    this.static = this.decodeStatic();
  }

  private chunk(offset: number, size: number): Uint8Array {
    return decompress(this.buf.subarray(offset, offset + size));
  }

  private decodeStatic(): GsqStatic {
    const n = this.header.nSplats;
    const raw = this.chunk(this.header.staticOffset, this.header.staticSize);
    const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
    const rgb = new Float32Array(n * 3);
    for (let i = 0; i < n * 3; i++) rgb[i] = readF16(dv, i * 2);
    const opOff = n * 3 * 2;
    const opacity = new Float32Array(n);
    for (let i = 0; i < n; i++) opacity[i] = raw[opOff + i] / 255;
    const scOff = opOff + n;
    const scales = new Float32Array(n * 3);
    for (let i = 0; i < n * 3; i++) scales[i] = readF16(dv, scOff + i * 2);
    return {
      nSplats: n,
      nFrames: this.header.nFrames,
      fpsHint: this.header.fpsHint,
      bboxMin: this.header.bboxMin,
      bboxMax: this.header.bboxMax,
      rgb, opacity, scales,
    };
  }

  /** Decompress one stored chunk into its (xyz, quat) int16 arrays. Absolute
   *  for v1 / v2 keyframes; modular deltas for v2 delta frames. */
  private payloadI16(idx: number): { xyz: Int16Array; quat: Int16Array } {
    const e = this.header.frames[idx];
    const raw = this.chunk(e.offset, e.size);
    const n = this.header.nSplats;
    const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
    const xyz = new Int16Array(n * 3);
    const quat = new Int16Array(n * 3);
    for (let i = 0; i < n * 3; i++) xyz[i] = dv.getInt16(i * 2, true);
    const qOff = n * 3 * 2;
    for (let i = 0; i < n * 3; i++) quat[i] = dv.getInt16(qOff + i * 2, true);
    return { xyz, quat };
  }

  decodeFrame(idx: number): GsqFrame {
    if (idx < 0 || idx >= this.header.nFrames) {
      throw new RangeError(`frame ${idx} out of range [0, ${this.header.nFrames})`);
    }
    const frames = this.header.frames;
    let xyzAbs: Int16Array;
    let quatAbs: Int16Array;

    if (this.header.version === 1 || (frames[idx].flags & 1)) {
      const p = this.payloadI16(idx); // absolute
      xyzAbs = p.xyz;
      quatAbs = p.quat;
    } else if (this.lastAbs && this.lastAbs.idx === idx - 1) {
      const d = this.payloadI16(idx); // delta
      xyzAbs = addI16(this.lastAbs.xyz, d.xyz);
      quatAbs = addI16(this.lastAbs.quat, d.quat);
    } else {
      // cold / scrub: walk forward from the nearest keyframe <= idx
      let kf = idx;
      while (kf > 0 && !(frames[kf].flags & 1)) kf--;
      const base = this.payloadI16(kf);
      xyzAbs = base.xyz.slice();
      quatAbs = base.quat.slice();
      for (let j = kf + 1; j <= idx; j++) {
        const d = this.payloadI16(j);
        xyzAbs = addI16(xyzAbs, d.xyz);
        quatAbs = addI16(quatAbs, d.quat);
      }
    }
    this.lastAbs = { idx, xyz: xyzAbs, quat: quatAbs };
    return dequantize(xyzAbs, quatAbs, this.header.bboxMin, this.span);
  }
}
