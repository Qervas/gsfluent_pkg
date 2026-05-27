/** Recenter a binary_little_endian PLY's vertex positions to the origin.
 *
 *  Spark packs each splat center as 3x float16. Float16 has ~10 mantissa
 *  bits, so its *absolute* step grows with distance from the origin: at a
 *  world coordinate of ~29000 (where our INRIA 3DGS scans live) the step is
 *  ~16 units — larger than the depth of a whole building. Every splat then
 *  snaps to one of a couple of grid planes and the model renders as "2
 *  layers". Subtracting the bbox centroid before Spark encodes brings the
 *  coordinates near the origin (max |coord| = span/2), restoring sub-cm
 *  precision while leaving every other splat attribute byte-identical.
 *
 *  Mutates `buffer` in place and returns it with the recentered bounds.
 *  Throws if the PLY is not a binary_little_endian point cloud with float
 *  x/y/z as the first element's first scalar properties (the caller falls
 *  back to handing the raw URL to Spark).
 */
export interface RecenteredPly {
  bytes: Uint8Array;
  /** Bounds AFTER recentering (symmetric about the origin). */
  min: [number, number, number];
  max: [number, number, number];
}

const TYPE_SIZE: Record<string, number> = {
  char: 1, uchar: 1, int8: 1, uint8: 1,
  short: 2, ushort: 2, int16: 2, uint16: 2,
  int: 4, uint: 4, int32: 4, uint32: 4, float: 4, float32: 4,
  double: 8, float64: 8,
};

function decodeAscii(bytes: Uint8Array): string {
  // PLY headers are pure ASCII; decode as latin1 so byte offsets == char offsets.
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return s;
}

export function recenterPly(buffer: ArrayBuffer): RecenteredPly {
  const bytes = new Uint8Array(buffer);

  // Locate end_header within a bounded prefix (headers are tiny).
  const scanLen = Math.min(bytes.length, 1 << 16);
  const prefix = decodeAscii(bytes.subarray(0, scanLen));
  const eh = prefix.indexOf("end_header");
  if (eh < 0) throw new Error("recenterPly: no end_header");
  let dataStart = eh + "end_header".length;
  if (bytes[dataStart] === 0x0d) dataStart++; // optional \r
  if (bytes[dataStart] === 0x0a) dataStart++; // \n
  else throw new Error("recenterPly: malformed end_header");

  const lines = prefix
    .slice(0, eh)
    .split("\n")
    .map((l) => l.replace(/\r$/, "").trim())
    .filter(Boolean);

  if (!lines.some((l) => /^format\s+binary_little_endian/.test(l)))
    throw new Error("recenterPly: not binary_little_endian");

  // Parse the FIRST element only; for INRIA 3DGS that is `vertex`.
  let vertexCount = -1;
  let inVertex = false;
  let stride = 0;
  let xOff = -1, yOff = -1, zOff = -1;
  let sawElement = false;

  for (const line of lines) {
    if (line.startsWith("element ")) {
      if (sawElement) break; // only the first element matters for the offset
      sawElement = true;
      const [, name, count] = line.split(/\s+/);
      if (name !== "vertex") throw new Error(`recenterPly: first element is ${name}, not vertex`);
      vertexCount = parseInt(count, 10);
      inVertex = true;
    } else if (inVertex && line.startsWith("property ")) {
      const parts = line.split(/\s+/);
      if (parts[1] === "list") throw new Error("recenterPly: list property in vertex");
      const size = TYPE_SIZE[parts[1]];
      if (!size) throw new Error(`recenterPly: unknown property type ${parts[1]}`);
      const propName = parts[2];
      if (propName === "x") xOff = stride;
      else if (propName === "y") yOff = stride;
      else if (propName === "z") zOff = stride;
      stride += size;
    }
  }

  if (vertexCount <= 0) throw new Error("recenterPly: empty vertex element");
  if (xOff < 0 || yOff < 0 || zOff < 0) throw new Error("recenterPly: missing x/y/z");

  const dv = new DataView(buffer);
  let minX = Infinity, minY = Infinity, minZ = Infinity;
  let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
  for (let i = 0; i < vertexCount; i++) {
    const base = dataStart + i * stride;
    const x = dv.getFloat32(base + xOff, true);
    const y = dv.getFloat32(base + yOff, true);
    const z = dv.getFloat32(base + zOff, true);
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
    if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
  }

  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const cz = (minZ + maxZ) / 2;
  for (let i = 0; i < vertexCount; i++) {
    const base = dataStart + i * stride;
    dv.setFloat32(base + xOff, dv.getFloat32(base + xOff, true) - cx, true);
    dv.setFloat32(base + yOff, dv.getFloat32(base + yOff, true) - cy, true);
    dv.setFloat32(base + zOff, dv.getFloat32(base + zOff, true) - cz, true);
  }

  return {
    bytes,
    min: [minX - cx, minY - cy, minZ - cz],
    max: [maxX - cx, maxY - cy, maxZ - cz],
  };
}
