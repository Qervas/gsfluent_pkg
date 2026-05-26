/** int16 dequantization, mirroring splat_ring.py :: _dequantize_i16.
 *    positions = bboxMin + (i16 + 32768) / 65535 * span
 *    qxyz      = i16 / 32767
 *    qw        = sqrt(clip(1 - sum(qxyz^2), 0, 1)); quat = [w, x, y, z]
 *  span = (bboxMax - bboxMin) with 0 components replaced by 1 (caller supplies).
 */
export function dequantize(
  xyzI16: Int16Array,
  quatI16: Int16Array,
  bboxMin: Float32Array,
  span: Float32Array,
): { positions: Float32Array; quats: Float32Array } {
  const n = xyzI16.length / 3;
  const positions = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < 3; c++) {
      const q = xyzI16[i * 3 + c];
      positions[i * 3 + c] = bboxMin[c] + ((q + 32768) / 65535) * span[c];
    }
  }
  const quats = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) {
    const qx = quatI16[i * 3 + 0] / 32767;
    const qy = quatI16[i * 3 + 1] / 32767;
    const qz = quatI16[i * 3 + 2] / 32767;
    let s = 1 - (qx * qx + qy * qy + qz * qz);
    if (s < 0) s = 0;
    else if (s > 1) s = 1;
    quats[i * 4 + 0] = Math.sqrt(s);
    quats[i * 4 + 1] = qx;
    quats[i * 4 + 2] = qy;
    quats[i * 4 + 3] = qz;
  }
  return { positions, quats };
}

/** IEEE-754 half (uint16) -> float32. Used as a fallback when
 *  DataView.prototype.getFloat16 is unavailable. */
export function halfToFloat(h: number): number {
  const sign = (h & 0x8000) >> 15;
  const exp = (h & 0x7c00) >> 10;
  const frac = h & 0x03ff;
  let val: number;
  if (exp === 0) {
    val = frac * Math.pow(2, -24);
  } else if (exp === 0x1f) {
    val = frac ? NaN : Infinity;
  } else {
    val = (1 + frac / 1024) * Math.pow(2, exp - 15);
  }
  return sign ? -val : val;
}

/** Read a little-endian half-float at byteOffset, preferring the native
 *  DataView.getFloat16 when present (modern browsers / Node >= 22). */
export function readF16(dv: DataView, byteOffset: number): number {
  const anyDv = dv as unknown as { getFloat16?: (o: number, le?: boolean) => number };
  if (typeof anyDv.getFloat16 === "function") {
    return anyDv.getFloat16(byteOffset, true);
  }
  return halfToFloat(dv.getUint16(byteOffset, true));
}
