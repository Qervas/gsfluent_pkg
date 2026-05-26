import { describe, it, expect } from "vitest";
import { modelPlyUrl } from "./api";

describe("modelPlyUrl", () => {
  it("builds the /api/models/file URL with the path query-encoded", () => {
    const u = modelPlyUrl("/data/scans/my scene/");
    // same-origin in tests (no VITE_BACKEND_URL) → path only, ?path encoded
    expect(u).toBe("/api/models/file?path=%2Fdata%2Fscans%2Fmy%20scene%2F");
  });
});
