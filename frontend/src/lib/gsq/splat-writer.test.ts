import { describe, it, expect } from "vitest";
import { splatArgs, makeSplatArgs } from "./splat-writer";
import type { GsqStatic, GsqFrame } from "./decoder";

const st: GsqStatic = {
  nSplats: 2, nFrames: 1, fpsHint: 24,
  bboxMin: new Float32Array([0, 0, 0]), bboxMax: new Float32Array([1, 1, 1]),
  rgb: new Float32Array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]),
  opacity: new Float32Array([0.7, 0.8]),
  scales: new Float32Array([0.01, 0.02, 0.03, 0.04, 0.05, 0.06]),
  deathFrame: null,
};
const frame: GsqFrame = {
  positions: new Float32Array([1, 2, 3, 4, 5, 6]),
  // quats are [w, x, y, z] per splat
  quats: new Float32Array([0.1, 0.2, 0.3, 0.4, 0.11, 0.22, 0.33, 0.44]),
};

describe("splatArgs", () => {
  it("maps splat 0: per-frame center/quat, static scale/color/opacity", () => {
    const out = splatArgs(frame, st, 0, makeSplatArgs());
    expect(out.center).toEqual([1, 2, 3]);
    // scales are read from a Float32Array -> compare with closeTo (f32 precision)
    expect(out.scales[0]).toBeCloseTo(0.01);
    expect(out.scales[1]).toBeCloseTo(0.02);
    expect(out.scales[2]).toBeCloseTo(0.03);
    // [w,x,y,z]=[0.1,0.2,0.3,0.4] -> three (x,y,z,w)=[0.2,0.3,0.4,0.1]
    expect(out.quat).toEqual([
      expect.closeTo(0.2), expect.closeTo(0.3), expect.closeTo(0.4), expect.closeTo(0.1),
    ]);
    expect(out.opacity).toBeCloseTo(0.7);
    expect(out.color).toEqual([
      expect.closeTo(0.1), expect.closeTo(0.2), expect.closeTo(0.3),
    ]);
  });

  it("maps splat 1 with the correct offsets + quat reorder", () => {
    const out = splatArgs(frame, st, 1, makeSplatArgs());
    expect(out.center).toEqual([4, 5, 6]);
    expect(out.scales[0]).toBeCloseTo(0.04);
    expect(out.scales[1]).toBeCloseTo(0.05);
    expect(out.scales[2]).toBeCloseTo(0.06);
    // [w,x,y,z]=[0.11,0.22,0.33,0.44] -> (x,y,z,w)=[0.22,0.33,0.44,0.11]
    expect(out.quat[3]).toBeCloseTo(0.11);
    expect(out.quat[0]).toBeCloseTo(0.22);
    expect(out.opacity).toBeCloseTo(0.8);
  });

  it("reuses the provided out object (no per-call allocation)", () => {
    const out = makeSplatArgs();
    const r = splatArgs(frame, st, 0, out);
    expect(r).toBe(out);
  });

  it("culls a splat at/after its death frame (opacity -> 0)", () => {
    // splat 1 dies at frame 3; splat 0 immortal (sentinel).
    const stD: GsqStatic = { ...st, deathFrame: new Uint16Array([0xffff, 3]) };
    // before death: full opacity.
    expect(splatArgs(frame, stD, 1, makeSplatArgs(), 2).opacity).toBeCloseTo(0.8);
    // at and after death: hidden.
    expect(splatArgs(frame, stD, 1, makeSplatArgs(), 3).opacity).toBe(0);
    expect(splatArgs(frame, stD, 1, makeSplatArgs(), 9).opacity).toBe(0);
    // the immortal splat is never culled.
    expect(splatArgs(frame, stD, 0, makeSplatArgs(), 9).opacity).toBeCloseTo(0.7);
  });

  it("defaults to visible when no frame index / no death channel", () => {
    // frameIdx defaults to 0, deathFrame null -> static opacity unchanged.
    expect(splatArgs(frame, st, 1, makeSplatArgs()).opacity).toBeCloseTo(0.8);
  });
});
