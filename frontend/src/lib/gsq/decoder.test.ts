import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { GsqDecoder } from "./decoder";

function fdir(name: string, file: string): string {
  return fileURLToPath(new URL(`./__fixtures__/${name}/${file}`, import.meta.url));
}
function bytes(name: string): Uint8Array {
  const b = readFileSync(fdir(name, "data.gsq"));
  return new Uint8Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
}
function f32(name: string, file: string): Float32Array {
  const b = readFileSync(fdir(name, file));
  return new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
}
function manifest(name: string): any {
  return JSON.parse(readFileSync(fdir(name, "manifest.json"), "utf8"));
}
function expectClose(a: Float32Array, b: Float32Array, eps = 1e-4) {
  expect(a.length).toBe(b.length);
  let maxDiff = 0;
  for (let i = 0; i < a.length; i++) maxDiff = Math.max(maxDiff, Math.abs(a[i] - b[i]));
  expect(maxDiff).toBeLessThanOrEqual(eps);
}

describe("GsqDecoder", () => {
  it("decodes the static block (drift)", () => {
    const d = new GsqDecoder(bytes("drift"));
    expectClose(d.static.rgb, f32("drift", "static_rgb.f32"));
    expectClose(d.static.opacity, f32("drift", "static_opacity.f32"));
    expectClose(d.static.scales, f32("drift", "static_scales.f32"));
  });

  it("decodes every frame in sequence to parity (drift)", () => {
    const d = new GsqDecoder(bytes("drift"));
    const m = manifest("drift");
    for (let i = 0; i < m.nFrames; i++) {
      const f = d.decodeFrame(i);
      expectClose(f.positions, f32("drift", `frame_${String(i).padStart(3, "0")}_pos.f32`));
      expectClose(f.quats, f32("drift", `frame_${String(i).padStart(3, "0")}_quat.f32`));
    }
  });

  it("sequential fast-path equals cold keyframe-walk (drift, frame 33)", () => {
    const seq = new GsqDecoder(bytes("drift"));
    for (let i = 0; i <= 32; i++) seq.decodeFrame(i); // lastAbs now at 32
    const fast = seq.decodeFrame(33);                 // 33 === 32+1 -> fast-path
    const cold = new GsqDecoder(bytes("drift")).decodeFrame(33); // fresh -> walk from kf 30
    expectClose(fast.positions, cold.positions, 0);
    expectClose(fast.quats, cold.quats, 0);
  });

  it("scrub: out-of-order decode matches expected (drift: 4, 1, 32)", () => {
    const d = new GsqDecoder(bytes("drift"));
    for (const i of [4, 1, 32]) {
      const pad = String(i).padStart(3, "0");
      const f = d.decodeFrame(i);
      expectClose(f.positions, f32("drift", `frame_${pad}_pos.f32`));
      expectClose(f.quats, f32("drift", `frame_${pad}_quat.f32`));
    }
  });

  it("modular int16 wraparound round-trips (wrap)", () => {
    const d = new GsqDecoder(bytes("wrap"));
    for (let i = 0; i < 3; i++) {
      const f = d.decodeFrame(i);
      expectClose(f.positions, f32("wrap", `frame_${String(i).padStart(3, "0")}_pos.f32`));
    }
  });

  it("throws on out-of-range frame index", () => {
    const d = new GsqDecoder(bytes("wrap"));
    expect(() => d.decodeFrame(99)).toThrow(/range/i);
  });

  it("decodes the death channel to match the manifest (death)", () => {
    const d = new GsqDecoder(bytes("death"));
    const m = manifest("death");
    expect(d.static.deathFrame).not.toBeNull();
    expect(Array.from(d.static.deathFrame!)).toEqual(m.deathFrame);
  });

  it("has a null death channel when absent (drift)", () => {
    expect(new GsqDecoder(bytes("drift")).static.deathFrame).toBeNull();
  });
});
