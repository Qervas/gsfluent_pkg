import { describe, it, expect } from "vitest";
import { dequantize, halfToFloat } from "./dequant";

describe("dequantize", () => {
  it("maps int16 endpoints to bbox endpoints", () => {
    // span = [2,2,2]; i16=-32768 -> bboxMin; i16=+32767 -> ~bboxMax.
    const bboxMin = new Float32Array([-1, -1, -1]);
    const span = new Float32Array([2, 2, 2]);
    const xyz = new Int16Array([-32768, -32768, -32768, 32767, 32767, 32767]);
    const quat = new Int16Array([0, 0, 0, 0, 0, 0]);
    const { positions, quats } = dequantize(xyz, quat, bboxMin, span);
    expect(positions[0]).toBeCloseTo(-1, 4); // (-32768+32768)/65535*2 - 1
    expect(positions[3]).toBeCloseTo(1, 4);  // (32767+32768)/65535*2 - 1
    // qxyz all 0 -> qw = 1
    expect(quats[0]).toBeCloseTo(1, 6);
    expect(quats[1]).toBeCloseTo(0, 6);
  });

  it("reconstructs qw from xyz so the quaternion is unit-length", () => {
    const bboxMin = new Float32Array([0, 0, 0]);
    const span = new Float32Array([1, 1, 1]);
    // qx = 16384/32767 ~ 0.5
    const { quats } = dequantize(
      new Int16Array([0, 0, 0]), new Int16Array([16384, 0, 0]), bboxMin, span,
    );
    const [w, x, y, z] = [quats[0], quats[1], quats[2], quats[3]];
    expect(w * w + x * x + y * y + z * z).toBeCloseTo(1, 5);
  });

  it("halfToFloat decodes known IEEE-754 half values", () => {
    expect(halfToFloat(0x3c00)).toBeCloseTo(1.0, 6);   // 1.0
    expect(halfToFloat(0x4000)).toBeCloseTo(2.0, 6);   // 2.0
    expect(halfToFloat(0x0000)).toBe(0);               // +0
    expect(halfToFloat(0xc000)).toBeCloseTo(-2.0, 6);  // -2.0
  });
});
