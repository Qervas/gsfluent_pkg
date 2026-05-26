// @vitest-environment happy-dom
import { describe, it, expect, vi } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useGsqPlayer } from "./use-gsq-player";
import type { GsqStatic } from "@/lib/gsq";

/** A fake Worker the hook talks to. Tests drive .onmessage manually. */
class FakeWorker {
  onmessage: ((e: MessageEvent) => void) | null = null;
  posted: any[] = [];
  terminated = false;
  postMessage(m: any) { this.posted.push(m); }
  terminate() { this.terminated = true; }
  emit(data: any) { this.onmessage?.({ data } as MessageEvent); }
}

const fakeStatic: GsqStatic = {
  nSplats: 4, nFrames: 10, fpsHint: 24,
  bboxMin: new Float32Array(3), bboxMax: new Float32Array(3),
  rgb: new Float32Array(12), opacity: new Float32Array(4), scales: new Float32Array(12),
};

describe("useGsqPlayer", () => {
  it("opens the url, reports progress, then ready with static", async () => {
    const w = new FakeWorker();
    const { result } = renderHook(() =>
      useGsqPlayer("http://x/a.gsq", () => {}, { createWorker: () => w as unknown as Worker }),
    );
    expect(w.posted[0]).toEqual({ type: "open", url: "http://x/a.gsq" });
    expect(result.current.status).toBe("loading");

    act(() => w.emit({ type: "progress", received: 50, total: 100 }));
    expect(result.current.progress).toBe(50);

    act(() => w.emit({ type: "ready", static: fakeStatic }));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.static?.nFrames).toBe(10);
  });

  it("requestFrame posts a frame message; frames invoke onFrame", () => {
    const w = new FakeWorker();
    const onFrame = vi.fn();
    const { result } = renderHook(() =>
      useGsqPlayer("http://x/a.gsq", onFrame, { createWorker: () => w as unknown as Worker }),
    );
    act(() => result.current.requestFrame(7));
    expect(w.posted).toContainEqual({ type: "frame", idx: 7 });

    const positions = new Float32Array(12);
    const quats = new Float32Array(16);
    act(() => w.emit({ type: "frame", idx: 7, positions, quats }));
    expect(onFrame).toHaveBeenCalledWith(7, { positions, quats });
  });

  it("surfaces worker errors and terminates on unmount", () => {
    const w = new FakeWorker();
    const { result, unmount } = renderHook(() =>
      useGsqPlayer("http://x/a.gsq", () => {}, { createWorker: () => w as unknown as Worker }),
    );
    act(() => w.emit({ type: "error", message: "boom" }));
    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe("boom");
    unmount();
    expect(w.terminated).toBe(true);
  });
});
