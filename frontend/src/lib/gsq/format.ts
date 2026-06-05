/** .gsq v2 header + frame-index parser. Source of truth:
 *  server/gsfluent/core/codecs/gsq.py :: parse_header_bytes
 *  frontend/python/splat_ring.py      :: _parse_gsq_header
 *  Layout (little-endian):
 *    0   magic "GSQ1"
 *    4   u32 version (1 or 2)
 *    8   u32 nSplats
 *    12  u32 nFrames
 *    16  f32 fpsHint
 *    20  3xf32 bboxMin
 *    32  3xf32 bboxMax
 *    44  u64 staticOffset
 *    52  u32 staticSize
 *    56  u64 deathOffset   (optional death channel; 0 = absent)
 *    64  u32 deathSize     (0 = absent)
 *    68  12 bytes reserved
 *    80  frame index: nFrames x <QII> = (offset u64, size u32, flags u32)
 */
export const MAGIC = "GSQ1";
export const HEADER_SIZE = 80;
export const INDEX_ENTRY_SIZE = 16;

export interface FrameEntry {
  offset: number;
  size: number;
  flags: number; // bit0 = is_keyframe
}

export interface GsqHeader {
  version: number;
  nSplats: number;
  nFrames: number;
  fpsHint: number;
  bboxMin: Float32Array; // length 3
  bboxMax: Float32Array; // length 3
  staticOffset: number;
  staticSize: number;
  /** Optional death channel: per-splat monotonic visibility cutoff
   *  (zstd'd uint16[nSplats]). deathSize === 0 means absent (older files /
   *  kill-radius disabled) — the decoder then treats every splat as immortal. */
  deathOffset: number;
  deathSize: number;
  frames: FrameEntry[];
}

export function parseHeader(u8: Uint8Array): GsqHeader {
  if (u8.byteLength < HEADER_SIZE) {
    throw new Error(`short header: ${u8.byteLength} bytes`);
  }
  const dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  const magic = String.fromCharCode(
    dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3),
  );
  if (magic !== MAGIC) throw new Error(`not a .gsq: magic=${JSON.stringify(magic)}`);

  const version = dv.getUint32(4, true);
  if (version !== 1 && version !== 2) {
    throw new Error(`unsupported .gsq version ${version}`);
  }
  const nSplats = dv.getUint32(8, true);
  const nFrames = dv.getUint32(12, true);
  const fpsHint = dv.getFloat32(16, true);

  const bboxMin = new Float32Array(3);
  const bboxMax = new Float32Array(3);
  for (let i = 0; i < 3; i++) {
    bboxMin[i] = dv.getFloat32(20 + i * 4, true);
    bboxMax[i] = dv.getFloat32(32 + i * 4, true);
  }
  const staticOffset = Number(dv.getBigUint64(44, true));
  const staticSize = dv.getUint32(52, true);
  // Reserved region (56..80). First 12 bytes = optional death-channel pointer.
  // Older files wrote zeros here, so deathSize === 0 reads as "absent".
  const deathOffset = Number(dv.getBigUint64(56, true));
  const deathSize = dv.getUint32(64, true);

  const indexEnd = HEADER_SIZE + nFrames * INDEX_ENTRY_SIZE;
  if (u8.byteLength < indexEnd) {
    throw new Error(`index incomplete: have ${u8.byteLength} need ${indexEnd}`);
  }
  const frames: FrameEntry[] = [];
  for (let i = 0; i < nFrames; i++) {
    const base = HEADER_SIZE + i * INDEX_ENTRY_SIZE;
    frames.push({
      offset: Number(dv.getBigUint64(base, true)),
      size: dv.getUint32(base + 8, true),
      flags: dv.getUint32(base + 12, true),
    });
  }
  return {
    version, nSplats, nFrames, fpsHint, bboxMin, bboxMax,
    staticOffset, staticSize, deathOffset, deathSize, frames,
  };
}
