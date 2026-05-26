import { describe, it, expect, vi, afterEach } from "vitest";
import { downloadGsq } from "./download";

afterEach(() => vi.restoreAllMocks());

describe("downloadGsq", () => {
  it("returns the body bytes and reports progress", async () => {
    const payload = new Uint8Array([1, 2, 3, 4, 5]);
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(payload, { status: 200, headers: { "content-length": "5" } }),
    ));
    const seen: Array<{ received: number; total: number | null }> = [];
    const buf = await downloadGsq("http://x/splats.gsq", (p) => seen.push(p));
    expect(new Uint8Array(buf)).toEqual(payload);
    expect(seen.at(-1)?.received).toBe(5);
    expect(seen.at(-1)?.total).toBe(5);
  });

  it("retries once on a transient failure then succeeds", async () => {
    const payload = new Uint8Array([9]);
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(new Response(payload, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const buf = await downloadGsq("http://x/splats.gsq", undefined, { retries: 1 });
    expect(new Uint8Array(buf)).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("throws on a non-OK response after exhausting retries", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 404 })));
    await expect(downloadGsq("http://x/splats.gsq", undefined, { retries: 0 }))
      .rejects.toThrow(/404/);
  });
});
