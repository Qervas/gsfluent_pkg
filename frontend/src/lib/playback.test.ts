import { describe, it, expect } from "vitest";
import { tickPlayback, nextFrame } from "./playback";

describe("nextFrame", () => {
  it("advances by one, wraps to 0 with loop, stops without loop", () => {
    expect(nextFrame(0, 5, true)).toBe(1);
    expect(nextFrame(4, 5, true)).toBe(0);
    expect(nextFrame(4, 5, false)).toBe("stop");
    expect(nextFrame(0, 1, true)).toBe("stop"); // <2 frames: nothing to play
  });
});

describe("tickPlayback", () => {
  const N = 5;
  const INT = 40; // 5 frames, 40ms/frame

  it("does not advance before the interval elapses", () => {
    const r = tickPlayback(0, 0, 16, INT, N, true, true);
    expect(r.advanced).toBe(false);
    expect(r.frame).toBe(0);
    expect(r.acc).toBe(16);
  });

  it("advances exactly one frame once the interval elapses", () => {
    const r = tickPlayback(0, 30, 16, INT, N, true, true); // 30+16=46 >= 40
    expect(r.advanced).toBe(true);
    expect(r.frame).toBe(1);
    expect(r.acc).toBe(0);
  });

  it("never skips: a huge dt still advances only one frame (stutter over skip)", () => {
    const r = tickPlayback(0, 0, 10_000, INT, N, true, true);
    expect(r.advanced).toBe(true);
    expect(r.frame).toBe(1); // not 250
    expect(r.acc).toBe(0);
  });

  it("wraps to 0 at the end when looping", () => {
    const r = tickPlayback(4, 0, INT, INT, N, true, true);
    expect(r.frame).toBe(0);
    expect(r.advanced).toBe(true);
  });

  it("stops at the last frame when not looping", () => {
    const r = tickPlayback(4, 0, INT, INT, N, true, false);
    expect(r.advanced).toBe(false);
    expect(r.stopped).toBe(true);
    expect(r.frame).toBe(4);
  });

  it("paused: accumulator stays at 0, no advance", () => {
    const r = tickPlayback(2, 99, 16, INT, N, false, true);
    expect(r.advanced).toBe(false);
    expect(r.frame).toBe(2);
    expect(r.acc).toBe(0);
  });

  it("single-frame sequence never advances", () => {
    const r = tickPlayback(0, 0, 9999, INT, 1, true, true);
    expect(r.advanced).toBe(false);
  });
});
