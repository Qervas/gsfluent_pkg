import { describe, expect, it } from "vitest";
import {
  computeEta,
  extractDetail,
  frameCountHint,
  isComposedRecipe,
  makeRunName,
} from "./run-submit";

describe("run-submit helpers", () => {
  it("detects composed in-memory recipes", () => {
    expect(isComposedRecipe({ _composed_from: { scenario: "earthquake" } })).toBe(true);
    expect(isComposedRecipe({ frame_num: 60 })).toBe(false);
    expect(isComposedRecipe(null)).toBe(false);
  });

  it("builds backend-safe run names", () => {
    const name = makeRunName(
      "model A",
      "★ earthquake·watermelon",
      new Date("2026-06-08T12:34:56.789Z"),
    );
    expect(name).toBe("model_A_earthquake_watermelon_2026-06-08T1234");
  });

  it("returns finite frame hints only", () => {
    expect(frameCountHint({ frame_num: 60 })).toBe(60);
    expect(frameCountHint({ frame_num: "12" })).toBe(12);
    expect(frameCountHint({ frame_num: "nope" })).toBeUndefined();
  });

  it("extracts simple and FastAPI-style error details", () => {
    expect(extractDetail('HTTP 422: {"detail":"bad recipe"}')).toBe("bad recipe");
    expect(extractDetail('HTTP 422: {"detail":[{"loc":["body","x"],"msg":"missing"}]}'))
      .toBe("body.x: missing");
    expect(extractDetail("network error")).toBe("network error");
  });

  it("computes ETA from first-frame time", () => {
    expect(computeEta(10, 30, 1_000, 11_000)).toBe("0:20");
    expect(computeEta(30, 30, 1_000, 11_000)).toBeNull();
    expect(computeEta(0, 30, 1_000, 11_000)).toBeNull();
  });
});
