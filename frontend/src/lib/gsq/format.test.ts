import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { parseHeader } from "./format";

function fixtureBytes(name: string): Uint8Array {
  const p = fileURLToPath(new URL(`./__fixtures__/${name}/data.gsq`, import.meta.url));
  const b = readFileSync(p);
  return new Uint8Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
}
function manifest(name: string): any {
  const p = fileURLToPath(new URL(`./__fixtures__/${name}/manifest.json`, import.meta.url));
  return JSON.parse(readFileSync(p, "utf8"));
}

describe("parseHeader", () => {
  it("parses header fields against the manifest (drift)", () => {
    const h = parseHeader(fixtureBytes("drift"));
    const m = manifest("drift");
    expect(h.version).toBe(2);
    expect(h.nSplats).toBe(m.nSplats);
    expect(h.nFrames).toBe(m.nFrames);
    expect(h.fpsHint).toBeCloseTo(m.fpsHint, 4);
    expect(Array.from(h.bboxMin)).toEqual(m.bboxMin.map((x: number) => Math.fround(x)));
    expect(h.frames.map((f) => f.flags)).toEqual(m.frameFlags);
  });

  it("frame offsets are strictly increasing and within the buffer (wrap)", () => {
    const bytes = fixtureBytes("wrap");
    const h = parseHeader(bytes);
    expect(h.frames.length).toBe(3);
    for (let i = 1; i < h.frames.length; i++) {
      expect(h.frames[i].offset).toBeGreaterThan(h.frames[i - 1].offset);
    }
    const last = h.frames[h.frames.length - 1];
    expect(last.offset + last.size).toBeLessThanOrEqual(bytes.byteLength);
    expect(h.frames[0].flags & 1).toBe(1); // frame 0 is a keyframe
  });

  it("rejects a bad magic", () => {
    expect(() => parseHeader(new Uint8Array(80))).toThrow(/not a \.gsq/);
  });
});
