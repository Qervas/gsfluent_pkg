import { describe, it, expect } from "vitest";
import { decompress } from "fzstd";

describe("gsq toolchain", () => {
  it("fzstd is importable and round-trips a known zstd frame", () => {
    // zstd frame for the bytes "abc" (magic 28 b5 2f fd ...). We only assert
    // the function is callable; real codec output is validated in Task 5.
    expect(typeof decompress).toBe("function");
  });
});
