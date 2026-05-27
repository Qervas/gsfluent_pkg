import { describe, it, expect } from "vitest";
import { recenterPly } from "./ply-recenter";

/** Build a binary_little_endian PLY with props `x y z f_dc_0` (all float32). */
function makePly(verts: Array<[number, number, number, number]>): ArrayBuffer {
  const header =
    "ply\n" +
    "format binary_little_endian 1.0\n" +
    `element vertex ${verts.length}\n` +
    "property float x\n" +
    "property float y\n" +
    "property float z\n" +
    "property float f_dc_0\n" +
    "end_header\n";
  const headerBytes = new TextEncoder().encode(header);
  const stride = 16; // 4 float32
  const buf = new ArrayBuffer(headerBytes.length + verts.length * stride);
  new Uint8Array(buf).set(headerBytes, 0);
  const dv = new DataView(buf);
  verts.forEach((v, i) => {
    const base = headerBytes.length + i * stride;
    dv.setFloat32(base + 0, v[0], true);
    dv.setFloat32(base + 4, v[1], true);
    dv.setFloat32(base + 8, v[2], true);
    dv.setFloat32(base + 12, v[3], true);
  });
  return buf;
}

function readVerts(bytes: Uint8Array, n: number): Array<[number, number, number, number]> {
  const ascii = String.fromCharCode(...bytes.subarray(0, 256));
  const start = ascii.indexOf("end_header") + "end_header\n".length;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out: Array<[number, number, number, number]> = [];
  for (let i = 0; i < n; i++) {
    const base = start + i * 16;
    out.push([
      dv.getFloat32(base + 0, true),
      dv.getFloat32(base + 4, true),
      dv.getFloat32(base + 8, true),
      dv.getFloat32(base + 12, true),
    ]);
  }
  return out;
}

describe("recenterPly", () => {
  it("recenters x/y/z about the bbox centroid and leaves other props untouched", () => {
    // Coordinates at the real-world magnitude that breaks float16 (~29000).
    const verts: Array<[number, number, number, number]> = [
      [3443, 29036, -19, 0.5],
      [3474, 29054, 30, -0.25],
    ];
    const { bytes, min, max } = recenterPly(makePly(verts));

    // Bounds come back symmetric about the origin (centroid removed).
    const span = [3474 - 3443, 29054 - 29036, 30 - -19];
    expect(min[0]).toBeCloseTo(-span[0] / 2, 3);
    expect(max[0]).toBeCloseTo(span[0] / 2, 3);
    expect(min[1]).toBeCloseTo(-span[1] / 2, 3);
    expect(max[2]).toBeCloseTo(span[2] / 2, 3);

    const got = readVerts(bytes, 2);
    // Recentered positions: original minus centroid (cx,cy,cz).
    const c = [(3443 + 3474) / 2, (29036 + 29054) / 2, (-19 + 30) / 2];
    expect(got[0][0]).toBeCloseTo(3443 - c[0], 3);
    expect(got[0][1]).toBeCloseTo(29036 - c[1], 3);
    expect(got[0][2]).toBeCloseTo(-19 - c[2], 3);
    // f_dc_0 untouched.
    expect(got[0][3]).toBeCloseTo(0.5, 5);
    expect(got[1][3]).toBeCloseTo(-0.25, 5);
  });

  it("throws on a non-binary_little_endian header (caller falls back to url)", () => {
    const ascii = new TextEncoder().encode(
      "ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n",
    );
    expect(() => recenterPly(ascii.buffer)).toThrow();
  });
});
